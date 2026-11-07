# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Plain HTML/CSS/JavaScript in `web/`, no build step (required by the organizers' guide: must be adaptable as a Dynamics 365 HTML component). Served by the FastAPI backend (`uvicorn cqa.api:create_app --factory`).

## Users

- Quality analyst: configures the checklist, loads dialogs, runs evaluation, reads results, files appeals.
- Reviewer: sees disputed scores with comment and dialog fragment, confirms or changes the score with a reason.
- Role switch in the header, no login (guide: auth not needed). The user had no preference on roles; two roles is our default from the guide.

## Product Purpose

Conversation Quality Analytics for Freedom Broker: evaluate operators in chats and calls (RU/KZ/EN) against a configurable checklist, show points, explanations and the exact turns behind each verdict, and run appeal → decision → recomputed total. Judged mainly on accuracy against organizers' ground truth, then features, usability and real processing.

## Operating Context

Hackathon defense: 5-minute live demo on a laptop and projector. Control step: new transcripts by group_id, one criterion replaced via the UI and re-applied.

## Capabilities and Constraints

- UI languages: Russian, English, Kazakh (switcher); dialog content shown in its own language.
- Desktop/projector first (≥1280 px); must not break on narrow screens.
- Evaluation takes ~100 s per dialog → background jobs with progress.

## Brand Commitments

User brief: Freedom Bank / Freedom Holding / Freedom Broker palette (logo green #06AE3C, neon mint #00C684–#06FCA8, deep greens on black, from the organizers' presentation) blended with a Doctor Doom–inspired green/black/steel scheme — colors only, no character imagery.

## Evidence on Hand

Accuracy numbers in progress.md (sealed test split 92.9% criterion agreement, MAE 4.04 on our own gold labels). No organizer ground truth, no testimonials — do not fabricate.
