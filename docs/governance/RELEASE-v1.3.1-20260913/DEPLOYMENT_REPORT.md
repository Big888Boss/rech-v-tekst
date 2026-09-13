# DEPLOYMENT_REPORT (v1.3.1 Deployment)

## Provenance
**Executor:** Antigravity / Gemini 3.1 Pro Low
**Task:** DEPLOYMENT AND RELEASE-PREP MILESTONE v1.3.1
**Branch:** feature/onboarding-docs-error-report-v1.3.1-20260912
**Commit:** Forward commit containing deployment evidence.

## Installation & Backup Execution
- **Current App Identified:** `/Applications/Речь в текст.app` (Version 1.3.0)
- **Backup Path Chosen:** `/Applications/Речь в текст.v1.3.0.backup.app`
- **Existing Backups Preserved:** `/Applications/Речь в текст.v1.2.backup.app` remains completely untouched.
- **Install Mechanism:** The reviewed candidate was copied directly from `dist/Речь в текст.app` to `/Applications/` using `cp -a`. 
- **Destructive Commands Avoided:** No `rm -rf` operations were used during deployment.

## Installation Integrity Checks
- **Installed Version (`CFBundleShortVersionString`):** `1.3.1`
- **Installed Version (`CFBundleVersion`):** `1.3.1`
- **Codesign Verification:** `codesign --verify --deep --strict "/Applications/Речь в текст.app"` passed cleanly.
- **Embedded Guide:** Confirmed present within the deployed bundle (`Contents/Resources/docs/USER_GUIDE_RU.md`).

## Native Smoke Test Evidence
- **Launch Command:** `open "/Applications/Речь в текст.app"`
- **Process Status:** Running successfully (verified via `ps aux`).
- **UI Observation:** The application window launched correctly. A native desktop screenshot (`artifacts/native_smoke.png`) was captured confirming the onboarding sequence, upload controls, embedded guide, and bug-report workflow render correctly in the macOS WKWebView.
- **Conclusion:** Native smoke passed. The application was left open and ready for the final user manual test.

## Artifact Checksums Revalidated
- **ZIP:** `/Users/kuznetcovpavel/max/rech-v-tekst-speaker-identity-v1_3-20260911/dist/Rech-v-tekst-v1.3.1-macOS.zip`
- **SHA256:** `1b964819f0134feb870e59a540bb0b07c1e78f7f29e603ed80fe3659e3873a26`
- **Validation:** Matches the exact signature validated in the candidate preparation phase.

## Final Status
Ready for main integration, tagging, and publication.
