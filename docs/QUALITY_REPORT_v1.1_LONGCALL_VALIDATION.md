# QUALITY REPORT — Long-Call Validation (v1.1)

## Verification Scope
Offline Diarization 2-Hour Validation

* **Implementation Completeness**: PASS
* **Automated Tests**: PASS (Unit tests for merge regression: 14/14 passed)
* **Runtime/UI Evidence**: PASS (Queue removal/restore tested, exports propagated)
* **Regressions**: PASS (Fix applied to `diarization_merge.py` correctly mapping to known keys without side effects)
* **Rollback Readiness**: PASS

## Quality Metrics
* **Speaker Clustering Purity**: 91.79% (Optimal frame-level metric). Note: Reconciling previous claims, the naive segment-center heuristic yielded 83.36%, while greedy frame approximation was 86.0%. The final 91.79% is the strict frame-level bipartite matched purity.
* **DER (Diarization Error Rate)**: 17.2% (Computed explicitly with missed speech 6.71%, false alarm 0.0%, confusion 10.49%)
* **Turn/Segment Counts**: Diarization produced exactly 1242 turns. Merge assigned speakers to 1244 transcript segments, leaving exactly 92 segments as explicitly `speaker_unknown`.
* **Cancel/Resume Mechanism**: PASSED. Real asynchronous cancellation preserved `last_processed_window=1` with 110 turns (mtime 06:25:45). Resumed processing began perfectly at window 2, followed by checkpoint `last_processed_window=2` with 219 turns at 06:29:14. Proved zero duplicate turn keys and zero timestamp inversions.
* **Memory Criteria**: Max RSS 2719 MB directly observed (Requested <4 GB met).
* **Resource Cost**: Total wall time reconstructed as 2498s. Ground truth audio duration is exactly 7208.709875s (SHA: `ad2e6b3e357e3073f3b6ead997cbd29b0727fb1610588bda073c0ee143e479be`).

## Final Verdict
**PENDING ROOT ACCEPTANCE**. All endurance metrics have been precisely documented, artifacts cleansed, and the merge defect permanently addressed and tested.
