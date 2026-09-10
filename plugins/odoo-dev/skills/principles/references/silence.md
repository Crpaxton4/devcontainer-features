# Rule of Silence

**Statement:** When a program has nothing surprising to say, it should say nothing.

## Rationale

Output carries an implicit signal: "this is worth your attention." A program that emits routine status messages, progress indicators, or verbose confirmations trains users to ignore its output. When the program then emits something genuinely important, the signal is lost in the noise.

Silence also supports composition. A program that emits nothing on success has output that is unambiguously meaningful: any output indicates something happened. This makes the output usable as input to another program without filtering.

The rule does not prohibit output — it prohibits *unsurprising* output. Error messages, warnings about unexpected conditions, and results the user asked for are all appropriate. Confirmation that routine operations completed as expected is not.

## Corollaries

- Exit codes are the primary success/failure signal for composable programs. Reserve stdout for results and stderr for diagnostics.
- A program that is silent on success and noisy on failure is trivially monitorable.
- Verbose modes (`-v`, `--debug`) are an appropriate escape hatch; silence should be the default.
- Log noise that operators must filter to find real events is a reliability problem: the real events will eventually be missed.

## Guards Against

- Alert fatigue: operators who ignore warnings because warnings are always present.
- Pipelines that require filtering output to find the signal.
- Programs whose output cannot be used as input because it mixes results with status messages.
