# UPGRADE_BACKLOG

This backlog contains exactly 8 fully specified product upgrades based on codebase inspection.

## Top 5 Upgrades
| Rank | Upgrade | User Value | Effort | Dependencies | Privacy / Security Risk | Acceptance Criteria | Release Grouping |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **P0** | **Crash Recovery & Checkpointing** | High | Med | Backend processing refactor | Low | Interrupted transcription resumes from last processed chunk automatically. No duplicated text output. | v1.4.0 |
| **P0** | **Long-call Diarization & Voice Identity** | High | High | Core diarization engine (pyannote) | Med (Speaker profiling) | Can process >1hr audio files with >90% speaker consistency. UI shows accurate progress. | v1.5.0 |
| **P1** | **Automated In-App Update Mechanism** | High | Med | GitHub Releases API, macOS permissions | Med (Arbitrary download/signing) | App notifies user of updates. 1-click download via verified GitHub release asset. | v1.6.0 |
| **P1** | **Local Model Management** | High | High | Settings UI, Disk I/O | Med (Storage limits/Path traversal) | Users can download, delete, and switch between Whisper models directly inside the settings UI. | v1.6.0 |
| **P1** | **Quality Confidence & Timestamps** | Med | Med | ASR model outputs | Low | Exported text includes word-level timestamps and color-coded low-confidence words. | v1.5.0 |

## Additional Upgrades
| Rank | Upgrade | User Value | Effort | Dependencies | Privacy / Security Risk | Acceptance Criteria | Release Grouping |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **P2** | **Export & Search** | Med | Low | Frontend JS, Browser APIs | Low | Users can export transcription as PDF/Word/SRT and use an in-app search bar to highlight text. | v1.6.0 |
| **P2** | **Accessibility Improvements** | Med | Low | HTML/CSS/ARIA | Low | App is fully navigable via keyboard. Screen readers correctly announce states (transcribing, done). | v1.6.0 |
| **P2** | **Performance & Storage Optimization** | Med | Med | Audio conversion pipeline | Low | Temp audio files are automatically purged on success. Audio chunking optimized for low RAM usage. | v1.7.0 |
