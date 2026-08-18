# Classical Alpha Loop Real-Data Validation

> Run date: 2026-08-14
> Immutable snapshot: 2026-08-07
> Evidence type: historical diagnostic, not pristine live OOS

## Result

All four declared account-scoped mainlines completed under
`purged_walk_forward_v7_balanced_anchor`. No candidate passed every ranker and
deployable-portfolio gate, so no model was promoted or attached to a formal
strategy.

| Market / scope | Rank IC | ICIR | Fixed Top-N net excess | Turnover | Deployable trades | Outcome |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| A-share / hs300 | 0.0073 | 0.0314 | -13.63% | 12.47x | 0 | Rejected |
| A-share / zz500 | 0.0152 | 0.0767 | -14.52% | 15.40x | 0 | Rejected |
| QDII / hk_exposure | 0.0827 | 0.2297 | +2.48% | 107.13x | 0 | Rejected |
| QDII / us_exposure | 0.1158 | 0.2847 | -17.97% | 128.84x | 0 | Rejected |

The A-share deployable tracks had valid isotonic calibration but no positive
cost-adjusted edges. QDII calibration was unavailable (`nonpositive` for Hong
Kong exposure and `flat` for US exposure), and both scopes also failed the
point-in-time audit and stability controls.

## Same-Window Arena

The corrected A-share arena compared both formal strategies, the candidate,
cash, equal-weight, low-volatility, and 20-session momentum on the same
2025-02-12 through 2026-07-10 window.

| Scope | Winner | Winner net excess | Defensive | Trend | Candidate |
| --- | --- | ---: | ---: | ---: | ---: |
| hs300 | 20-session momentum baseline | +21.03% | -7.27% | -5.74% | -13.20% |
| zz500 | 20-session momentum baseline | +8.12% | -17.91% | -12.98% | -22.56% |

This does not authorize changing the model to copy the winner. The final
window has now been inspected repeatedly and is contaminated for further
selection. Its useful conclusion is that the learned residual did not add
robust value and the formal strategies still carry excess complexity and cash
drag relative to the simple baseline.

## Operational Acceptance

- One current spec per market and account scope: passed.
- Version identity includes spec hash and training protocol: passed.
- Same-date cache invalidates on data, portfolio contract, spec, or protocol
  changes; `--force` performs a real rebuild: passed.
- Ranking diagnostic and deployable tracks are both persisted and displayed:
  passed.
- Return attribution reconciles cash drag, selection, and execution cost:
  passed in tests.
- Promotion safety: passed; all four candidates remain rejected.

The next valid quality evidence must come from future observations or a newly
frozen untouched holdout. Historical results above may be used for diagnosis,
not another parameter loop.
