"""Passenger entry point for shared hosting; cron owns result synchronization.

a2wsgi dispatches HTTP requests without ASGI lifespan events, so initialize the
database schema explicitly during application creation. Initialization is
idempotent and preserves the installed SQLite results snapshot.
"""
import asyncio
import os
import threading

from a2wsgi import ASGIMiddleware


TASK_KEY = "lotirex.asgi_task"


class PassengerApplication(ASGIMiddleware):
    """Retain a2wsgi's bridge and clean up disconnected streaming requests."""

    def __init__(self, app, **kwargs):
        async def dispatch(scope, receive, send):
            environ = scope["wsgi_environ"]
            environ[TASK_KEY] = asyncio.current_task()
            try:
                await app(scope, receive, send)
            finally:
                environ.pop(TASK_KEY, None)

        # a2wsgi normally starts its event-loop thread in the constructor. A
        # Passenger application can be imported by a preloader and forked only
        # afterwards; threads do not survive that fork, so requests in the
        # worker would wait forever on the preloader's loop. Supplying an
        # unstarted loop here lets the first request start it in the process
        # that actually serves traffic.
        supplied_loop = kwargs.pop("loop", None)
        self._manage_loop = supplied_loop is None
        self._loop_pid = None if self._manage_loop else os.getpid()
        self._loop_thread = None
        self._loop_lock = threading.Lock()
        super().__init__(dispatch, loop=supplied_loop or asyncio.new_event_loop(), **kwargs)

    def _ensure_worker_loop(self):
        if not self._manage_loop or self._loop_pid == os.getpid():
            return
        with self._loop_lock:
            process_id = os.getpid()
            if self._loop_pid == process_id:
                return
            if self._loop_pid is not None:
                # The application was used before Passenger forked. Its old
                # loop thread exists only in the parent, so the child needs a
                # fresh loop rather than the inherited loop object.
                self.loop = asyncio.new_event_loop()
            worker = threading.Thread(
                target=self.loop.run_forever,
                name="lotirex-wsgi-loop",
                daemon=True,
            )
            worker.start()
            self._loop_thread = worker
            self._loop_pid = process_id

    def __call__(self, environ, start_response):
        self._ensure_worker_loop()
        response = super().__call__(environ, start_response)
        try:
            yield from response
        finally:
            response.close()
            task = environ.get(TASK_KEY)
            if task is not None:
                # a2wsgi waits for application completion after a fully drained
                # response. Closing its iterator early skips that path; cancel
                # the tracked ASGI task so its file handles are still released.
                async def cancel_request():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                asyncio.run_coroutine_threadsafe(cancel_request(), self.loop).result(timeout=self.wait_time)


def create_application(*, loop: asyncio.AbstractEventLoop | None = None) -> PassengerApplication:
    # Enforce this before importing the ASGI application, even if the hosting
    # account inherited the development scheduler setting.
    os.environ["LOTIREX_SCHEDULER"] = "0"
    from . import sync
    from .app import app
    from .deployment import ensure_database

    ensure_database()
    sync.initialize()
    return PassengerApplication(app, wait_time=10, loop=loop)
