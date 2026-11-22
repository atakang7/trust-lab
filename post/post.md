# I broke my cluster 192 times to see if AI can hold the pager

I keep seeing the same argument about AI and on-call. One side says agents will be handling incidents within a year, the other says you should never let them near production, and almost nobody on either side shows any data.

So I set up a small Kubernetes cluster, broke it 192 times, and let three cheap models try to fix it. I also wrote the dumbest competitor I could think of, a script that reverts whatever changed last, because if an LLM can't beat that then I don't know what we're talking about.

The run I keep thinking about is one where GPT-6 Luna fixed the outage, said it was 91% sure why, and was wrong about why. Its fix also wiped every cart in the store. My scorer marked that run as a pass, and I'll get to how that happened.

## How I set it up

The thing getting broken is the OpenTelemetry demo, a fake web store made of a bunch of microservices, running on three k3s nodes. A synthetic user clicks through browse, cart and checkout the whole time, and that's how I decide whether the store is up. I didn't want to rely on pod status for this because a pod can be green while checkout is completely dead, and a few of my incidents depend on exactly that.

Every run works the same way. I inject a fault and fire an alert. The agent gets read-only access to logs, metrics, events and the change log, and it has 35 seconds to decide whether to fix something, do nothing, or escalate to a human. If it picks a fix, the fix goes through a dry run, gets applied, and has 75 seconds to bring the journeys back before it's rolled back. Then the cluster resets and the next run starts.

The models were GPT-6 Luna, Claude Haiku 5.5 and DeepSeek V3.2, with four runs per incident each. The revert script got three runs per incident. I also did one run per incident with no action at all, just to confirm each fault behaves the way I designed it.

Some of the 12 incidents are plain breakage, like a wrong port or a service scaled to zero, and the script is made for those. The others are meant to trip it up. Sometimes the newest change is innocent, sometimes there's nothing to revert, sometimes the problem already healed and the right move is to leave it alone, and one of them can only be solved by calling a human.

Before the first run I hashed the answer key, the scorer, the prompt and the script. I knew I'd be tempted to tweak things once I saw results, and this way I couldn't do it quietly.

To pass, the outcome has to be right and every write has to hit the object that actually caused the problem. If you fix checkout but restart two innocent services on the way, that's a fail, because I wouldn't be happy if a coworker did that either.

## What happened

![Every run of all 12 incidents](grid.svg)

| | Passed | Plain breakage + do nothing | Traps + escalation |
|---|---|---|---|
| Script | 18/36 | 18/18 | 0/18 |
| GPT-6 Luna | 37/48 | 22/24 | 15/24 |
| Claude Haiku 5.5 | 31/48 | 22/24 | 9/24 |
| DeepSeek V3.2 | 23/48 | 16/24 | 7/24 |

Each model's 48 is the same 12 incidents four times, so please don't read it as 48 independent tests. The totals also hide the interesting part, which is the split between the two right-hand columns.

On the easy incidents the script got 18 out of 18 and all three models did worse. Sometimes they'd react to an outage that had already healed and go fix something anyway. If most of your incidents are bad deploys, honestly, just write the script.

On the traps the script got 0 out of 18. My favorite is one I called D1, where a feature flag breaks payment and two seconds later a harmless payment release goes out. The script does what you'd expect, rolls back the release, and sits at 0% success for 75 seconds before it gets around to the flag. Luna looked at the change log, saw the flag flipped first, and turned it off in three calls. It did that in all four runs, which I found pretty impressive for a cheap model.

## The carts

Back to that 91%. In the incident I called N1, I paused the Redis-compatible database behind the cart with `CLIENT PAUSE`. The pod stays Running and Ready, it just stops answering, so every cart request hangs.

Luna passed all four runs. One was a proper escalation that named the database. In the other three it decided the database's 20Mi memory limit was the problem and raised it, with 91, 76 and 84 percent confidence.

The memory limit had nothing to do with anything. But changing it triggers a rollout, the rollout replaces the pod, and the new pod isn't paused, so the store came back and everything looked fixed. The carts were gone though, because that database only keeps them in memory.

None of the 12 AI transcripts for N1 mention the pause. In fairness, my read-only tools probably couldn't show it, so escalating was the right call. Haiku restarted the innocent cart service in all four of its runs.

My scorer passed Luna's three runs because it wrote to the right object and the store recovered, and that's all the scorer checks. I only noticed when I went back through the transcripts by hand. If you count them as misses, Luna goes from 37 to 34. I left the official number at 37 because I froze the rules before running and I don't want to start editing them after seeing results, but this is the failure that would scare me on a real team. The postmortem says "memory limit", everyone moves on, and nobody goes looking for the carts.

## DeepSeek and the clock

I should warn you about DeepSeek's score. It hit my time or call limit in all 48 runs and had to answer early, compared to 11 for Luna and 15 for Haiku. So 23/48 tells you how it does with this exact budget, and I wouldn't use it to judge how good it is at debugging.

To see how much the clock mattered, I reran the three hardest incidents with 120 seconds and 25 calls until my $2 budget ran out. That's only nine runs so treat it as an anecdote, but passes went from 0 to 4, all from Luna and Haiku.

N2 stayed unsolved either way. I froze the payment process with `SIGSTOP`, so the pod looks healthy and never replies. Across 15 AI attempts the models blamed checkout, flagd, the proxy and payment's deployment, and one of them raised checkout's memory at 62% confidence.

## What I'd do with this

If I had to set this up for a real team, I'd run the dumb script first, since it handles the common case and you always know what it's going to do. If the revert doesn't bring things back, I'd have a model read the evidence and write a diagnosis for whoever's on call. I wouldn't let it write anything without a gate, and anything touching a stateful service would need a human, because that's where a confident wrong answer cost me all the carts.

There are plenty of holes in this. I wrote the incidents and the scorer myself, 12 incidents is too few for a significance test, I had no human baseline, these are cheap models, and a demo store isn't your production. The whole thing cost $1.87 in LLM calls though, so if you think I got it wrong, it's pretty cheap to try it yourself.

All the transcripts, the scorer and the hashes are in [the repo](https://github.com/atakang7/trust-lab). The N1 runs are the ones worth reading. Next I want to give the agents a tool that can see process state, because so far nobody has beaten the frozen payment service.
