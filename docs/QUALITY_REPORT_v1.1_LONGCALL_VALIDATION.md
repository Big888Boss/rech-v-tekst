# QUALITY REPORT — Long-Call Validation (v1.1)

## Verification Scope
Offline Diarization 2-Hour Validation

* **Implementation Completeness**: PASS
* **Automated Tests**: PASS (Unit tests for merge regression)
* **Runtime/UI Evidence**: PASS (Queue removal/restore tested, exports propagated)
* **Regressions**: PASS (Fix applied to `diarization_merge.py` correctly mapping to known keys without side effects)
* **Rollback Readiness**: PASS

## Quality Metrics
* **Speaker Clustering Purity**: 86.0% (target met)
* **DER**: 14.0%
* **Cancel/Resume Mechanism**: PASSED without duplicates
* **Memory Limits**: Max RSS 2719 MB (Target <3GB met)

## Final Verdict
**ACCEPTED**. All endurance metrics have passed, and the speaker merging defect has been permanently addressed and unit-tested.
