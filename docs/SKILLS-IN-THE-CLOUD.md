# Skills and the cloud: what we learned

How to build a skill so it works everywhere, and how to get your local skills into cloud Claude Code sessions. Written from what happened while making the `ai-layer-scaffold` skill (see `.claude/skills/ai-layer-scaffold/`).

## 1. The key fact

A cloud session runs on a separate, temporary machine. **It cannot see your computer**, so a skill that only lives in `C:\Users\<you>\.claude\skills\` does not exist for the cloud. It also cannot read your local clones, such as `C:\Users\<you>\GitHub\agentic-coding-course`.

## 2. Where a cloud session can find skills

| Place | Seen by the cloud? | Lasts | Use it for |
|---|---|---|---|
| Your computer, `~/.claude/skills/` | No | Forever, on your computer only | Local Claude Code |
| **Your claude.ai skills** (Settings, Capabilities, Skills) | **Yes, in every session** | Until you remove it | Skills you want everywhere |
| **The project repo**, `.claude/skills/<name>/` | **Yes, in sessions on that repo** | Forever (it is in git) | Skills tied to one project |
| The session's own `~/.claude/skills/` (installed during a session) | Only that session | Gone when the session ends | A quick try-out |
| A zip attached to the chat | Only if Claude unpacks it | That session | One-off use |

A skill installed into the session's `~/.claude/skills/` was recognised straight away in the same session. It was not there in the next session.

## 3. How to get a local skill into the cloud

| Goal | Steps |
|---|---|
| Every cloud session | Zip the skill folder (the folder itself at the top of the zip, with `SKILL.md` inside it), then upload it in claude.ai under Settings, Capabilities, Skills. Claude cannot do this step for you. |
| One project only | Copy the folder into the repo as `.claude/skills/<name>/`, commit and push. |
| Just this session | Attach the zip to the chat and ask Claude to install it. |
| Check it worked | Ask "list my skills". The new one should appear with its description. |

## 4. How to build a skill that works anywhere

| Rule | Why |
|---|---|
| Folder name = skill name. Put `SKILL.md` at the top, scripts in `scripts/` | This is the layout the zip upload and the repo both expect. |
| `SKILL.md` starts with `name:` and `description:` between `---` lines | Claude reads the description to decide when to use the skill. |
| Put trigger phrases in the description ("add the AI layer", "set this project up like the others") | The better the phrases, the more reliably it starts when you ask. |
| Never hard-code your own machine paths (`C:\Users\...`) | They do not exist in the cloud or on another computer. Take a `--source` argument, an environment variable, and a list of usual places instead. |
| Keep scripts to the standard library (for example Python only) | The cloud machine may not have your packages. |
| Make the script safe by default: dry run, never overwrite, and print what it did | You can check before anything changes, and run it twice without harm. |
| Test it with a stand-in folder before the real thing | We caught several problems this way (for example a Windows line-ending change in `run.bat`). |
| Say plainly what the skill needs from outside | The AI layer needs a clone or zip of the course repo. |

## 5. The catch with private repos

The course repo (`dynamous-community/agentic-coding-course`) could not be cloned from the cloud: it asked for a login, and connecting it to the session failed. A cloud session only reaches repos your GitHub connection allows (see the docs: https://code.claude.com/docs/en/claude-code-on-the-web).

| Option | Result |
|---|---|
| Attach a zip of the repo to the chat | Works. Use `--source <file>.zip`. |
| Connect the repo to your Claude GitHub access | Should work, not tested here. |
| Copy what you need into your own repo | Works. Then the skill needs no outside source. |

## 6. Mistakes to avoid (from this session)

| Mistake | Fix |
|---|---|
| Reusing a branch name that a pull request was already using, then resetting it | The pull request lost a commit. Check `git log` before resetting, and keep new work on a new branch. |
| Editing a `.bat` file with a tool that rewrote the line endings | Keep CRLF in Windows batch files. Check with `file run.bat`. |
| Running Linux commands in PowerShell (`cd /d`) | PowerShell uses `cd <path>`. `cd /d` is for the older Command Prompt. |
| Assuming a pull brought the update because the page looked old | Check the version label in the header and the `run.bat` log. |
