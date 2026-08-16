# What changes are held to

A checklist for reviewing changes here, including your own. Much of it is aimed at the
failure modes that show up when code is written quickly or generated — plausible-looking code
that papers over problems rather than solving them. The tooling catches some of it; the rest
needs a person.

Items marked **[CI]** are enforced automatically. Do those first, since they cost nothing.

## Errors

1. **[CI]** No bare `except:` or `except Exception:` without either re-raising or a logged,
   specific reason. (ruff `E722`, `BLE001`)
2. No swallowed errors. If something is caught and ignored, a comment says why that is safe.
3. `try` blocks stay small. Wrapping twenty lines means you cannot tell which one failed.
4. **No retry or fallback added to make a symptom go away.** Every retry answers: what
   transient condition is this for? A fallback that hides a bug is worse than the bug, because
   now it is invisible. This is the single most common failure in generated code.
5. **[CI]** Every network call has a timeout.
6. Error paths have tests, not just code.
7. **[CI]** `CancelledError` is always re-raised.

## Tests

8. Every test has at least one assertion that could fail. `assert result is not None` on
   something that is never None is not a test.
9. No test asserts on a mock's own return value. That tests the mock.
10. Nothing mocks a module we wrote and then asserts only on the mock.
11. No mocks of services that do not exist. Check every mock target resolves.
12. Tests are not edited in the same commit that changes the code they cover unless the commit
    message says why. "Made the test pass" is not a fix.
13. **[CI]** One behaviour per test. Several unlabelled assertions in a row make failures hard
    to read.
14. Something covers each of: no results, 403, 429, an expired token, a malformed response, a
    locked database.

## Dependencies

15. **[CI]** Every import resolves and every third-party package is declared. (`uv sync
    --locked`, mypy)
16. **[CI]** Nothing declared that nothing imports. (`deptry`)
17. Each runtime dependency earns a sentence somewhere saying why it is here. Target: no more
    than a dozen.
18. No API called that is not in the installed version's documentation. Check the signature,
    do not remember it.
19. **[CI]** `uv.lock` is committed and CI runs `--locked`.

## Shape

20. No abstract base class, protocol or factory with exactly one implementation.
21. No class whose only method is `__init__` plus one function. That is a function.
22. No dependency injection container, plugin registry or event bus in a single-process app.
23. **[CI]** No dead code: unreachable branches, unused functions, commented-out blocks.
    (ruff `F401`, `ARG`, `ERA`, `vulture`)
24. Four layers is the budget: fetch, decide, store, deliver. If a change adds a fifth,
    it needs a reason.
25. No near-identical blocks in two modules.
26. **[CI]** Complexity limits. (ruff `C901`, `PLR`)

**New abstractions need a justification in the pull request.** One line is enough. This exists
because the reliable tell of generated code is structure that anticipates requirements nobody
has.

## Configuration and secrets

27. **[CI]** No tokens, webhook URLs, proxy credentials or cookies in source or in a committed
    `.env`. (`gitleaks`)
28. `.env.example` lists every variable; `.env` is ignored by git.
29. **One** configuration mechanism: the `Settings` class. No `os.getenv` scattered elsewhere.
30. No setting that nothing reads, and no read of a setting that is not declared.
31. Secrets are `SecretStr` so they cannot appear in logs or tracebacks.
32. No secret interpolated into a log line, including inside a URL.

## Async

33. **[CI]** No blocking call inside a coroutine: `time.sleep`, `requests`, synchronous
    SQLite, blocking file reads. (ruff `ASYNC`)
34. No mixing threads and asyncio without a documented reason.
35. No `create_task` whose result is dropped on the floor — those get garbage collected
    mid-flight. Use the task group or hold a reference.
36. Nothing unbounded: no queue without a limit, no task per item without a cap.
37. Sleeps are interruptible. `await asyncio.sleep(60)` in a loop means the container takes a
    minute to shut down and gets killed instead.

## Words

38. **[CI]** Consistent naming. No `data`, `result`, `temp`, `process_data2`.
39. No comment or docstring describing behaviour the code does not have.
40. **The README does not claim anything that does not exist.** Every feature in it maps to a
    code path, and preferably to a test. Overpromising is the fastest way to lose people's
    trust, and it is the most common artefact of writing documentation before the code
    settles.

## Two habits worth more than the list

**If you cannot explain a change line by line without rereading it, do not merge it.** Code
nobody understands is code nobody can fix later.

**Prefer making a bad state impossible to handling it.** The version of this app that checks
whether a session exists in nine places is worse than the one where getting a session creates
it. Every guard you add is a guard someone has to keep true.
