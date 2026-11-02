# ADR 0013: Forecast models must beat seasonal-naive, or the baseline is used

Status: Accepted (implemented in M3); the margin and benchmark are under review (see Consequences)

## Context
The project requires simple baselines first and honest uncertainty. With roughly 117 mature days of daily totals,
learned models can easily overfit, and a forecast that is worse than "same weekday last week" is worse than useless.

## Decision
- Candidates: naive, 7-day moving average, seasonal-naive, 4-week seasonal mean, Holt-Winters (damped trend),
  ridge and shallow gradient boosting on lag features expressed relative to the trailing 28-day mean. No XGBoost or
  deep learning: the data does not support them.
- A rolling-origin backtest (expanding window, >= 60 training days, 14-day horizon, weekly steps) scores every model out of sample.
- A learned model is selected only if its pooled MAE beats seasonal-naive by >= 5% **and** it wins more than half the
  folds; otherwise the best baseline is used and the reason is stored.
- Models see only history up to their origin (leakage is impossible by construction and tested).
- Prediction intervals come from the chosen model's out-of-sample relative errors; coverage is checked leave-one-fold-out.
- ACOS is derived from the spend and sales forecasts. A model that fails to fit is excluded and reported.
- Forecasts below 74 mature days are refused. Results are stored immutably in `forecast_runs`.

## Consequences
First held-out evaluation: mean improvement over seasonal-naive 7.8% and 85% of campaign cases not worse, but the
selected model was no worse in only 6 of 10 account cases (needed 7), and a learned model won 129 of 130 times.
This suggests noisy selection from few backtest folds and a weak benchmark. Candidate changes (larger margin,
benchmark against the 4-week mean, averaging top models) must be validated on fresh seeds. Forecasting costs about
3 s per series and runs off the event loop; it is a background-job concern at campaign scale.
