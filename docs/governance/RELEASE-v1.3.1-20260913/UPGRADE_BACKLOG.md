# UPGRADE_BACKLOG

This backlog captures 8 concrete product upgrades based on codebase inspection and planned goals.

| Rank | Upgrade | Value / Effort | Dependencies | Risk | Acceptance Criteria | Release Grouping |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **P0** | **Crash Recovery & Checkpointing** | High Value / Med Effort | Backend processing refactor | Low | Interrupted transcription resumes from last processed chunk. No duplicated output. | v1.4.0 (Core Stability) |
| **P0** | **Long-call Diarization & Voice Identity** | High Value / High Effort | Core diarization engine | Med (Privacy) | Can process >1hr audio files with >90% speaker consistency. UI shows progress accurately. | v1.5.0 (Feature Release) |
| **P1** | **Quality Confidence & Timestamps** | Med Value / Med Effort | ASR model outputs | Low | Exported text includes word-level timestamps and color-coded low-confidence words. | v1.5.0 (Feature Release) |
| **P1** | **Local Model Management** | High Value / High Effort | Settings UI, Disk I/O | Med (Storage limits) | Users can download, delete, and select different Whisper models inside the app UI. | v1.6.0 (UX & Scale) |
| **P1** | **Automated In-App Update Mechanism** | High Value / Med Effort | GitHub Releases API, macOS permissions | Med (Security/Signing) | App notifies user of updates and can download/replace itself seamlessly (or link to ZIP). | v1.6.0 (UX & Scale) |
| **P2** | **Export & Search** | Med Value / Low Effort | Frontend JS | Low | Users can export transcription as PDF/Word/SRT and use a search bar to highlight text. | v1.6.0 (UX & Scale) (Quick Win) |
| **P2** | **Accessibility Improvements** | Med Value / Low Effort | HTML/CSS/ARIA | Low | App is fully navigable via keyboard. Screen readers correctly announce states (transcribing, done). | v1.6.0 (UX & Scale) (Quick Win) |
| **P2** | **Performance & Storage Optimization** | Med Value / Med Effort | Audio conversion | Low | Temp audio files are automatically purged on success. Audio chunking optimized for RAM usage. | v1.7.0 (Performance) |

## Notes
- **Quick Wins:** Export & Search, Accessibility Improvements.
- **Architectural Changes:** Crash Recovery, Local Model Management.
- Do not implement these features until assigned a specific milestone.
