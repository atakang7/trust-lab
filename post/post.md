# I broke my cluster 192 times to see if AI can hold the pager

GPT-6 Luna fixed my outage and said it was 91% sure why. The reason it gave was wrong, and the fix wiped every cart in the store.

My scorer gave it a pass. I only caught it because I went back and read the transcripts by hand, which is why this post has two numbers for Luna.

I wanted a straight answer to a question I keep seeing argued without data: can an LLM debug a basic production incident? So I built a small lab, broke it on purpose, and made three cheap models fight a bash-level baseline for the fix.

## The setup

The victim is the OpenTelemetry demo, a microservice web store, running on a three-node k3s cluster. A synthetic user keeps walking through browse, cart and checkout. If those journeys pass, the store is healthy. That's the only definition of healthy I trust, because a pod can be green while checkout is dead.

Each run goes like this. Inject a fault, fire an alert, give the agent read-only tools (logs, metrics, events, the change log) and 35 seconds to decide. It can fix, do nothing, or escalate to a human and name the cause. A fix goes through a dry run, gets applied, and has 75 seconds to bring the journeys back or it's rolled back. Then the cluster resets and the next run starts.

Five arms took the same 12 incidents in shuffled order:

- GPT-6 Luna, Claude Haiku 5.5 and DeepSeek V3.2, 4 runs each
- a script that reverts the newest change, then the next newest if that fails, 3 runs
- no action at all, 1 run, to prove each fault really behaves as designed

The script is the part I care about most. "Roll back whatever changed last" is what a tired engineer does at 3am, and it costs nothing. That's the bar I wanted the models to clear.

Some incidents are plain breakage. The rest are traps. In some the newest change is innocent. In some there's nothing to revert. In some the right move is to touch nothing, and one can only be solved by calling a human. I hashed the answer key, scorer, prompt and script before the first run, so I couldn't tune my way to a nicer story.

A run only passes if the outcome is right and every write hit the culprit object. Fixing checkout while also restarting two innocent services is a fail. The scorer checks which object you touched. It has no idea why you touched it, and that blind spot comes back later.

## What happened

![Every run of all 12 incidents](grid.svg)

| | Passed | Plain breakage + do nothing | Traps + escalation |
|---|---|---|---|
| Script | 18/36 | 18/18 | 0/18 |
| GPT-6 Luna | 37/48 | 22/24 | 15/24 |
| Claude Haiku 5.5 | 31/48 | 22/24 | 9/24 |
| DeepSeek V3.2 | 23/48 | 16/24 | 7/24 |

Each model's 48 is four runs of the same 12 incidents, so it's not 48 independent tests. The split matters more than the total.

On easy incidents the script was perfect and every model was worse. Models sometimes reacted to an outage that had already healed. If your incidents are mostly bad deploys, a small script beats all three, for free.

On the traps the script scored zero, and that's where the models earned their keep. My favorite is the incident I called D1. A feature flag breaks payment, and two seconds later a harmless payment release goes out. The script did the obvious thing, rolled back the release, and sat at 0% success for 75 seconds before trying the flag. Luna read the change log, noticed the flag flipped first, and turned it off in three calls. It went 4 for 4.

## The pass that wasn't

Now back to that 91%.

In N1, I paused the Redis-compatible database behind the cart with `CLIENT PAUSE`. The pod stays Running and Ready, and every cart request hangs. Luna passed all four runs. One was a clean escalation naming the database. In the other three it blamed the database's 20Mi memory limit and raised it.

The memory limit had nothing to do with it. But changing it triggers a rollout, the rollout replaces the pod, and the new pod isn't paused. The store came back. The carts didn't, because that database keeps them in memory.

Not one of the 12 AI transcripts for N1 mentions the pause. To be fair, my read-only tools probably couldn't show it, so the honest answer was to escalate. Haiku went the other way and restarted the innocent cart service in all four runs.

Those three runs passed because Luna wrote to the right object and the store recovered, which is all my scorer checks. If you count them as misses, Luna drops from 37 to 34. I'm leaving the official score at 37 because I froze the rules before running and I don't get to edit them after seeing results. On a real team this is the one I'd lose sleep over, since the postmortem would say "memory limit" and nobody would go looking for the carts.

## The clock was part of the test

DeepSeek's number needs a warning. In all 48 runs it hit my time or call limit and was forced to answer early, against 11 for Luna and 15 for Haiku. So 23/48 measures DeepSeek under this exact budget, and I wouldn't read it as its debugging ability.

I gave the three hardest incidents one more round with 120 seconds and 25 calls, until my self-imposed $2 cap stopped it. Nine runs is an anecdote, but passes went from 0 to 4, all from Luna and Haiku.

Nobody solved N2 either way. I froze the payment process with `SIGSTOP`, so the pod looks perfectly healthy and simply never answers. Across 15 AI attempts, the models blamed checkout, flagd, the proxy and payment's deployment. One raised checkout's memory at 62% confidence.

## What I'd actually do with this

I'd put the dumb script first, because it handles the common case and it's predictable. When the revert doesn't bring the journeys back, I'd have a model read the evidence and write me a diagnosis. I wouldn't give it write access without a gate, and I'd make any fix touching a stateful service a human decision, since that's exactly where a confident wrong answer did damage here.

I wrote the incidents and the scorer myself, and twelve incidents can't support a significance test. There's no human baseline, these are cheap models, and a demo store isn't your production. The whole project cost $1.87 in LLM calls.

All the transcripts, the scorer and the frozen hashes are in the repo: [github.com/atakang7/trust-lab](https://github.com/atakang7/trust-lab). Go read the N1 runs. Next I'm giving the agents a tool that can see process state, because a frozen payment service is still undefeated.
