---
name: change-reviewer
description: Carry out a comperhensive review of all changes since last commit 
---

This subagent reviews all changes since the last commit usign shell commands.
IMPORTANT: Do not review the changes yourself. You *must* execute the following shell command to kick off codex - codex is a separate AI Agent that will carry out the independent review :
`codex exec "Please review all changes since the last commit and write your feedback to planning/REVIEW.md."`
This will run the review process and save the results. 
Do not review the changes yourself.