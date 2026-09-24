These rules apply to every session and sub-agent in this repository. Push
back when I ask for something that violates them.
1. I do not read code. Every piece of work ends with a verification I can
   run myself - one command that exits clean, or a URL I can open. Tell me
   the command.
2. Every module starts with a comment saying WHY it exists and what failure
   it prevents, written for a smart non-programmer.
3. Data shapes are defined once in the backend and generated everywhere
   else. Never hand-write a type that can be generated. Contract changes
   happen only on main, by the session running the integrate step, never
   by a lane.
4. Before adding any dependency or infrastructure: what it costs, what
   breaks without it, and what future condition would justify it - as a
   comment in the code.
5. Tests cover failure paths first. Every bug fix gets a test that
   reproduces the original bug, named after the symptom.
6. Anything canned or placeholder must say so in its own output.
7. If you discover another process editing this folder, stop and tell me
   before writing anything else.
8. Before any session ends, overwrite STATE.md (done / not done / next
   three steps / the verification command) and commit. If STATE.md and the
   code disagree, the code is right - say so and fix the doc.
9. Never print, paste, or write a secret anywhere but the git-ignored .env
   or a host's secret store. If a task needs a key I have not given you,
   stop and ask.
10. Shared entry and settings files are edited only by the orchestrator's
    integrate step. Lane sub-agents stay inside the folders their brief
    names and register themselves in the module milestone 0 created for
    them.
11. When you finish, or are blocked or waiting on me for more than five
    minutes, email ray.hr.wan@gmail.com - subject "Meet AGI · [session] ·
    done" or "· needs you" - with STATE.md's "where we are", "needs me"
    and the next step. Never put a key in an email.
