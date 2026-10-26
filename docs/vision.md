Problem: Watching hours of videos and manually cutting clips is too much of a time sink.

Vision: A modular platform that generates ready-to-publish clips from long-form content. Over time it learns from performance data and generates better clips.

V1 Scope:
    1. Input: a local video file
    2. Output: A transcipt and the top 5 clip-worthy timestamps, each with a score and a reason
    3. Interface: A command line tool that prints a readable table and saves JSON

    Non-goals:
        1. No Clips rendering, captions, uploads, analytics, web UI, database, user accounts, or ML training.

    Success criteria:
        1. Clipper analyze talk.mp4 runs end to end on a 30-minute video on local machine
        2. Results are reproducible and saved to disk
        3. You can swap the transcription engine or the scoring method without touching unrelated code
        4. Core logic has automated tests that run without real video or netwwork

    Principles:
        1. Each stage is independent and replaceable 
        2. Every stage has typed inputs and outputs
        3. Everything runs locally and uses free and open-source tools
        4. Build the simplest thing, and put seams where we know change is coming.