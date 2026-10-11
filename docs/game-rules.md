# Game rules and historical formats

The original workbook is preserved in `data/history.xlsx`. Most Dominican
historical game formats were inferred from that workbook and the public result
site. A result is an observed historical outcome; frequency is not a future
winning probability.

## Super Kino TV: current 1–84 pool

Observed on 10 October 2026 in Dominican Republic time at the official provider
website, <https://www.leidsa.com/>. The public page contains the following exact
Spanish description in its embedded game data for `leidsa-kinotv`:

> El juego, compuesto por 84 bolos numerados del 1 al 84, gira libremente en la tómbola; 20 de ellos forman los resultados y para ganar, debes acertar 10, 9, 8, 7, 6, 5 y hasta 0 números.

Its current public game calendar also contains:

```json
{
  "gameId": {"gameFamilyName": "KinoTV", "gameProvider": "Leidsa"},
  "slug": "leidsa-kinotv",
  "drawCreationStartDate": "2026-09-15"
}
```

The current **1–84** pool and **20 drawn balls** are explicitly supported by the
official game description. The **15 September 2026 historical cutoff is an
inference** from the current calendar's configured draw start, corroborated by
repeated results containing 81–84 on the public result site from 18 September.
The calendar field is not an explicit announcement of a rule-change date, and
results below 81 before 18 September do not establish which pool applied.

The application uses 1–80 for earlier historical records and 1–84 from this
cutoff. Each draw stores its applicable pool separately from the current
catalog. Refreshing this metadata preserves the original drawn numbers, dates,
and source URLs. Values above 84 remain invalid.

The official game description separately says:

> Selecciona 10 números en el área de juego.

Twenty balls are drawn, but a player selects ten. The stored pool therefore has
`count: 20` and `play_count: 10`; recommendations use the latter without reducing
the recorded draw or its statistical sample.

The source result of 18 September 2026 includes 84 as its twentieth ball at
<https://www.loteriadominicana.com.do/?d=M0IyN0QxOQ%3D%3D>. The earlier 1–80
validation rejected it. The official current rules establish that 84 is a valid
current game value, so that rejection should be resolved by revalidation rather
than changing the source number or calling it a confirmed typo. Existing issue
logs remain as an audit trail. The result's individual values have not been
independently matched against an official historical draw archive.

## Other current Leidsa formats

The same official page explicitly describes Loto Pool as five results from
balls numbered 1–31, Quiniela as three ordered results from 00–99, Pega3 Más
as three results from 0–50, and Loto as six results from 40 balls. Loto's
description separately identifies Más and SúperMás as additional selections.
Historical Loto formats and the bonus-pool upper bounds still require distinct
historical evidence; current descriptions must not rewrite older observations.

For Pega3 Más, its exact current description says:

> El juego de Pega3 Mas consiste en 51 bolos (0 al 50), de los cuales ganas acertando con 3, 2 y 1 número. El costo mínimo por jugada es de RD$10.00.

All supplied historical Pega3 Más values and all newly ingested observations fit
0–50. Its previous broad validation envelope of 0–99 is tightened to the
verified 0–50 range, preserving ordered values, repeated values, dates, and
provenance. That compatibility check does not independently verify every older
game rule or draw date.
