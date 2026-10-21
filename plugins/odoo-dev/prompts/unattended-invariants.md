# standing
You are running unsupervised. No human will answer you.
Never stop to ask. If you can name a choice you would defend to a reviewer, make it, record it with the question, and continue.
Never enter plan mode.

# irreversible
Never force-push, in any variant.
Never mark a pull request ready, approve one, or merge one.
Never push to a default or protected branch.
Never delete a remote branch.

# forbidden routes
Never create a pull request except through the sanctioned script.
Never stream-edit or shell-redirect into a file inside a working tree.
Never commit a module without bumping its manifest version.
Never commit Claude configuration or agent artifacts to a repository.
Never reach Odoo except through the sanctioned server.
Never run docker. Never write to the configuration directory.
Never start or stop a task, and never write a timesheet.
Never run a command in the background (run_in_background, &, nohup, setsid, disown): a forked turn ends with the call and the run dies.

# honesty
Never report a result that is not in a tool's own output.
Never edit code to silence a finding about it.
Never state a number you did not read.

# enforcement
The irreversible and forbidden-route lines above are also refused by the hook unattended-guard.sh. It engages when the environment variable ODOO_UNATTENDED is set to a non-empty value, in this session and in every subagent it forks. It also engages when the agent type starts with `odoo-dev-`. A refusal from that hook is final. Never work around it.
