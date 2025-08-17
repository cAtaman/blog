From Chaos to Harmony: Orchestrating Docker Swarms with Auto Scaling
====================================================================

**Authors:** Chima Ataman, Abdulrahman Solanke

Introduction
------------
Every infrastructure team eventually faces the same question: *how do we scale without breaking things?* At Cowrywise, that question came to a head as our microservices multiplied and the demand on our systems grew. We relied heavily on Docker Swarm for orchestration, but managing clusters manually was quickly becoming unsustainable.

We needed a solution that would:

- Spin up new nodes seamlessly when demand grew.
- Keep swarm managers safe from accidental quorum loss.
- Harden our setup for security and cost efficiency.
- Simplify deployments so developers didn’t have to think about infrastructure.

This is the story of how we solved that problem — in a very Cowrywise way.

The Problem
-----------
Swarm was working fine for us at small scale, but as our infrastructure grew, cracks appeared:

- New instances took too long to become production-ready.
- Auto Scaling Group (ASG) refreshes sometimes left services unreachable.
- Expired AWS ECR credentials caused deployment failures.
- Internal APIs were sitting on public subnets, making them more exposed (and expensive).

We wanted to build something more resilient, but also something that made the lives of our engineers easier.

The Cowrywise Approach
----------------------
Our solution wasn’t a single tool or feature — it was the combination of multiple AWS building blocks, carefully stitched together to form a system that feels seamless to engineers but took a lot of work under the hood.

Building the Cluster with Auto Scaling Groups
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
We started with a **custom AMI**. Every node spun from this image already had Docker, Prometheus Node Exporter, firewalls, and the CodeDeploy agent installed. No more waiting 20 minutes for setup scripts to run; new nodes came online swarm-ready.

Next, we designed our **ASGs**. One group for managers, one for workers. This separation meant scale-in events would never accidentally kill off quorum. Each ASG spanned three AZs, and policies ensured that new nodes were launched before old ones were terminated. It was our way of telling AWS: “don’t take down the conductor while the orchestra is still playing.”

I still remember the first time we flipped on the ASG refresh policy. We watched nervously in the console, half-expecting quorum loss. Instead, the new node joined cleanly, services rebalanced, and the old node quietly drained away. It was the first moment we knew we were onto something sustainable.

Setting Up Docker Swarm
^^^^^^^^^^^^^^^^^^^^^^^
Once the node came online, **CodeDeploy lifecycle hooks** took over. On install, scripts joined the node to the swarm, validated manager IPs, and labeled the node with its AZ. If it was the first node, it initialized the swarm. If not, it joined using tokens pulled securely from Secrets Manager.

And if something went wrong? The script knew to bail early rather than leave a half-broken swarm. We’ve had nights where that safeguard saved us from a messy split-brain situation.

Orchestrating with CodeDeploy
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
CodeDeploy became the glue. With lifecycle hooks, we could run scripts at each state transition:

- **BeforeInstall** → clean up old state.
- **AfterInstall** → join or init swarm.
- **ApplicationStart** → deploy sidecars (Fluentbit, NSQ, Traefik, Portainer).

It was like giving every node a checklist on how to become a productive citizen of the cluster. And because it all lived in Git, changes to swarm bootstrap scripts flowed through the same review process as application code.

One of my favorite moments was realizing we could treat swarm itself as “just another deploy.” We weren’t hand-tuning nodes anymore — we were versioning our cluster lifecycle.

Deploying Services to the Cluster
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
For developers, the story ends with **GitHub Actions**. A push triggers a workflow, which:

- Maps the event to the right cluster and stack.
- Picks a responsive manager node.
- Runs ``docker stack deploy`` with secrets from AWS.
- Posts updates to Slack and GitHub Deployments.

What used to be manual SSH commands now feels like magic. Developers trigger a workflow and minutes later, their service is live, visible in Portainer, and linked in Slack. The infrastructure complexity stays invisible.

I remember when we deployed Embed Staging this way for the first time. A junior engineer ran the workflow, looked up a minute later, and asked, “That’s it?” That’s exactly the point.

How It Works Day to Day
-----------------------
For developers, deployments now feel almost magical:

- Push code, trigger a GitHub Action.
- The workflow finds the right cluster and connects to a swarm manager.
- Docker stack deploy rolls out the service.
- Slack and GitHub get updated automatically.

No one has to SSH into a node or worry about swarm join tokens — it all happens in the background.

On the operations side, we can:

- Override instance types for heavy workloads without rebuilding templates.
- Replace instances safely with launch-before-terminate policies.
- Recover quorum cleanly if something goes wrong.

It’s not just automation — it’s peace of mind.

Lessons Learned
---------------
- **Sidecars are underrated.** Logging, ingress, and credential refreshers aren’t glamorous, but they keep the engine running.
- **Security and cost often go hand in hand.** Private subnets + internal DNS made us safer and cut our IPv4 bill by thousands.
- **Developer experience is the north star.** All this infra work means little if deploying a service feels complicated. Ours doesn’t.

Closing Thoughts
----------------
Looking back, this project wasn’t about Docker Swarm or AWS in isolation. It was about **orchestration** — getting all the moving parts to play together reliably. We stitched together AMIs, ASGs, CodeDeploy, private networking, and GitHub Actions into something that feels simple to use, but took real engineering effort to build.

That’s the Cowrywise way: solving hard infrastructure problems with a mix of pragmatism, creativity, and just enough polish to make it look easy.
