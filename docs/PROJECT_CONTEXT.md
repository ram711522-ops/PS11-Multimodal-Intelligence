
# PS11 - PROJECT CONTEXT

## 1. Project Overview

Project Name:
Real-Time Multimodal Misinformation Detection
and Digital Verification Platform.

Problem Statement:
Develop an intelligent platform that detects and
analyses misinformation using multimodal media
analysis, claim verification, historical media
matching, coordinated account detection and
misinformation propagation analysis.

The platform must provide evidence-backed findings
through an investigation dashboard.

## 2. Core Functional Requirements

### A. Media Authenticity Analysis
- Image analysis.
- Video frame extraction and analysis.
- Audio preprocessing and analysis.
- Integrate genuine pretrained detectors where
  available and properly evaluated.

### B. Historical Media Detection
- Identify previously circulated images.
- Use perceptual hashing (pHash).
- Maintain a local historical reference index.
- Return candidate matches with source and date.
- No match does not prove an image is new.

### C. Claim Verification
- Accept text claims.
- Retrieve relevant evidence.
- Identify supporting and contradicting evidence.
- Produce evidence-linked statuses:
  Verified, Contradicted, Misleading, Unverified.
- Never invent evidence or citations.

### D. Coordinated Account Detection
- Represent accounts and posts as graph nodes.
- Analyse shared media, similar captions and
  posting-time proximity.
- Use NetworkX for graph analysis.
- Explain why accounts are grouped.
- Suspicious coordination is not proof of bots
  or malicious intent.

### E. Propagation Analysis
- Process timestamped post events.
- Track event propagation.
- Generate timelines and visualisations.
- Do not claim unrestricted live social media access.

### F. Multilingual Analysis
- Prioritise English, Hindi and Telugu.
- Preserve original text.
- Evaluate multilingual processing quality.

### G. Investigation Dashboard
- Accept investigation submissions.
- Display media analysis.
- Display evidence and claim status.
- Display account graph and propagation timeline.
- Display processing status and errors.
- Retrieve previous investigations.

## 3. Team Responsibilities

### Ram - Backend and Platform Lead
Git branch: member-1

Responsibilities:
- Repository architecture.
- FastAPI backend.
- SQLite database.
- Investigation creation and management.
- Media upload and validation.
- API contracts.
- Historical matching integration.
- Video and audio preprocessing.
- WebSocket status updates.
- Backend integration and testing.

### Santosh - Intelligence and Frontend Lead
Git branch: member-2

Responsibilities:
- Claim verification.
- Evidence retrieval.
- Multilingual baseline.
- Account and post graph.
- Coordination analysis.
- Propagation analysis.
- React dashboard.
- Graph and timeline visualisation.
- Frontend testing.

## 4. Technology Stack

Frontend: React
Backend: Python FastAPI
Database: SQLite
Image processing: Pillow and OpenCV
Image similarity: ImageHash / pHash
Graph analysis: NetworkX
Realtime communication: WebSockets

Use only dependencies that are required.
Check compatibility before installing packages.

## 5. Proposed Project Architecture

backend/
    app/
    routes/
    schemas/
    services/
    database/

media_engine/
    image/
    video/
    audio/
    historical/

intelligence/
    claims/
    evidence/
    graph/
    propagation/

frontend/
tests/
data/
docs/

The architecture is a target structure.
Inspect the existing repository before creating
or changing directories.

## 6. API Contracts

POST /api/investigations
Create an investigation.

POST /api/investigations/{id}/media
Upload media.

GET /api/investigations/{id}
Retrieve investigation details and status.

GET /api/investigations/{id}/results
Retrieve analysis results.

POST /api/historical-index
Add a permitted reference image.

POST /api/evidence
Add evidence records.

POST /api/feed/events
Submit controlled account/post events.

GET /api/investigations/{id}/graph
Retrieve graph analysis.

WS /ws/investigations/{id}
Receive processing and status updates.

These are proposed contracts.
Agree on exact request and response schemas
before implementing integrations.

## 7. Engineering Principles

- Build real functionality, not UI-only demonstrations.
- Never fabricate model outputs.
- Never hardcode verdicts as AI predictions.
- Never invent accuracy or confidence values.
- Record the method used for every analysis.
- Validate uploaded files.
- Handle unsupported formats safely.
- Maintain source and evidence traceability.
- Use modular and understandable code.
- Write tests for critical functionality.
- Document limitations honestly.

## 8. Development Workflow

Understand
    ->
Design
    ->
Implement
    ->
Test
    ->
Analyse actual results
    ->
Fix defects
    ->
Review and approve

Do not proceed to the next major feature
until the current feature has been tested.

## 9. Git Collaboration

Ram works on member-1.
Santosh works on member-2.
main contains integrated and tested code.

Use small commits and Pull Requests.
Avoid editing shared files simultaneously.
Never merge untested changes.

## 10. Implementation Priority

Priority 1:
Working backend, database and investigation
submission workflow.

Priority 2:
Image upload and historical matching.

Priority 3:
Evidence retrieval and claim verification.

Priority 4:
Account graph and coordination analysis.

Priority 5:
Video/audio preprocessing and available
authenticity detectors.

Priority 6:
Connected dashboard and realtime updates.

Priority 7:
End-to-end testing and documentation.

## 11. Definition of Done

The application must:
- Start successfully.
- Accept real investigation submissions.
- Persist investigation records.
- Perform actual implemented analysis.
- Display traceable evidence.
- Handle unsupported inputs.
- Display real processing status.
- Pass critical tests.
- Preserve investigation history.

An unfinished or unvalidated capability must
be clearly marked as experimental or deferred.

## 12. Role of Codex

Codex is a development assistant.

Before major implementation:
1. Inspect the repository.
2. Explain the proposed design.
3. Identify dependencies.
4. Explain testing strategy.
5. Wait for approval.

After implementation:
1. Show changed files.
2. Explain the code.
3. Run tests.
4. Report actual test results.
5. Explain remaining limitations.

Never push, merge or delete branches without
explicit approval from Ram.
