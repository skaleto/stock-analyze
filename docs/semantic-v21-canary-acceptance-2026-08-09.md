# Semantic V21 Simple Canary Acceptance

## Conclusion

The final provider-neutral V21 contract passed a three-document simple canary
with both Claude Code and the DeepSeek API. Both executors produced the same two
event types and one no-event outcome, and every document passed the complete
local schema, evidence, IR, mention-compilation, and lifecycle checks.

This is a harness compatibility canary only. It does not approve V21 for
production, import any event, change a binding qualification, or demonstrate
model/strategy uplift.

## Frozen Contract

- Semantic contract hash: `90c8dc7ee3a0cff33dfa94f5b29c1762c4aff5b858bd9f75f92564363a51b792`
- Profile: `a-share-announcement-mentions-v21`
- Prompt: `semantic-mentions-v16`
- Document IR: `announcement-document-ir-v1`
- Retriever: `deterministic-evidence-v1`
- Compiler: `mention-compiler-v3-ir`
- Same semantic tasks: 3
- Separate immutable execution jobs: yes
- Import performed: no

## Samples

| Document | Expected result | Contract pressure |
| --- | --- | --- |
| 2026 half-year earnings flash | `earnings_flash`, `completed` | Multilevel table headers, raw scalar, unit and period lineage |
| Share buyback plan | `buyback`, `approved` | Amount range, price cap, approval date and separately cited status |
| Board secretary working rules | no event | Genuine governance document with no supported event |

## Results

| Executor | Accepted documents | Events | No-event | Quarantined | Repairs | Dropped items | Tokens | Provider latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Claude Code / `claude-fable-5` | 3/3 | 2 | 1 | 0 | 0 | 0 | 22,570 | 97.485 s |
| DeepSeek API / `deepseek-v4-pro` | 3/3 | 2 | 1 | 0 | 0 | 0 | 15,512 | 11.827 s |

Both executors preserved `621,408,705.13` revenue and `38,544,455.63` net
profit as exact table scalars. The deterministic IR supplied unit `元` and
period `本报告期`. Both independently cited `发布` for the earnings lifecycle
and `审议通过` for the buyback lifecycle.

## Defect Found During Canary

The first DeepSeek run omitted buyback status while the old compiler inferred
`approved` from fact evidence. That violated the frozen rule that lifecycle may
only come from separately cited status evidence. The final implementation now:

1. registers and validates status evidence independently;
2. forbids V21 lifecycle inference from titles, fact evidence, or surrounding
   uncited text;
3. requires the universal prompt to copy explicit publication, disclosure,
   approval, signing, completion, implementation, revision, cancellation, and
   investigation phrases;
4. versions the stricter implementation as `mention-compiler-v3-ir`.

The final rerun passed for both executors without provider-specific prompt
branches.

## Evidence

- Machine report: `.artifacts/semantic-v21-canary/canary_report.json`
- Portable task manifest: `.artifacts/semantic-v21-canary/portable_fixture.json`
- Claude frozen job: `.artifacts/semantic-v21-canary/jobs/claude/`
- DeepSeek frozen job: `.artifacts/semantic-v21-canary/jobs/deepseek/`

## Remaining Production Gates

Before activation, run the larger stratified canary and pre-acceptance set from
the approved design, measure severe-error and quarantine rates by event family
and document difficulty, validate canonical persistence and point-in-time
lineage, then run paired Base versus Base+Event model evaluation. Agreement
between Claude and DeepSeek is not Gold and is not itself a promotion signal.
