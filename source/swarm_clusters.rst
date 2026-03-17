.. toctree::


=============================================================
From Buzz to Balance: Docker Swarm Orchestration Without Tears
=============================================================


Something Isn't Right Here
--------------------------
You know the feeling. You SSH into a production server at 11pm because a deployment failed, and the running config doesn't match what's in the repo. A process was tuned by hand months ago. The environment has drifted in ways no one fully tracks. You patch it. You go to bed. You do it again next Thursday.

That was us. Not in some dramatic, everything-is-on-fire way. More like a slow accumulation of workarounds that had quietly become the system. We had infrastructure that *worked*, technically. But it didn't work the way infrastructure should: predictably, boringly, without someone's personal knowledge of which server does what.

This is the story of how we moved from that to something we actually trust. Not by chasing the shiniest orchestration tool on the market, but by picking the one that fit: Docker Swarm, and building the automation around it that made the difference.


Where We Started
-----------------
Like most growing platforms, our infrastructure had evolved organically. Over time, we'd accumulated multiple ways of deploying services: some ran as raw code on dedicated instances, others as standalone Docker containers, and a few had been loosely grouped into an early Swarm cluster without much structure around it. Each approach had its own deployment scripts, its own conventions, and its own failure modes.

The setup worked, but it didn't scale in the way that mattered most: operationally. Adding a new service meant choosing which deployment model to follow, and debugging an incident meant knowing which model that particular service used. Observability was fragmented across multiple tools with inconsistent coverage.

We needed a single deployment model, a consistent networking layer, and infrastructure that didn't depend on any one person's knowledge of how a specific server was configured.


The Elephant in the Room
------------------------
Yes, we picked Docker Swarm. In 2026. We've heard the jokes.

Here's the thing: Kubernetes is an extraordinary piece of engineering. It is also, for a team our size managing the number of services we run, an extraordinary amount of operational overhead. We didn't need service meshes, custom resource definitions, or a dedicated platform team to manage the orchestrator itself. We needed containers, rolling updates, service discovery, and overlay networking. Swarm gives us all of that with a tool our engineers already know: the Compose file.

A Swarm compose file *is* a Docker Compose file with a ``deploy`` key. There's no new DSL to learn, no Helm charts to template, no YAML-in-YAML to debug at 2am. Our developers write the same ``docker-compose.yml`` they use locally, add deployment constraints and resource limits, and that's the production manifest.

.. code-block:: yaml
   :linenos:

    deploy:
      update_config:
        parallelism: 1
        order: start-first
        failure_action: rollback
        monitor: 30s
      rollback_config:
        failure_action: pause
        order: stop-first

``start-first`` gives us zero-downtime rolling updates. ``failure_action: rollback`` with a 30-second ``monitor`` window means a bad deployment automatically rolls back, and if the rollback itself fails, it pauses so a human can investigate instead of looping. You'll see the full picture, with placement, resource limits, and networking, in a complete Compose file later. Could Kubernetes do more? Absolutely. Do we need more? Not today.

We're not ideological about this. The obvious risk is that Docker has largely stopped investing in Swarm; the project receives minimal updates. But for our purposes, that's a feature, not a bug. Swarm is stable *because* it's done. We'd rather run a finished tool than babysit a moving target. If we outgrow it, we'll migrate. But "you might need Kubernetes someday" is not a reason to adopt it now. Not when the operational tax is real and the benefits are theoretical for our workload.


Rewiring the Foundation
-----------------------
The migration wasn't a big bang. It was a series of deliberate changes across networking, compute, deployment, and observability, each one load-bearing enough that we couldn't afford to rush.

**Private subnets, finally.** We moved all compute off public subnets and into private ones (one ``/24`` CIDR block per availability zone). Services that don't need internet exposure are no longer reachable from the internet, full stop. NAT Gateways handle outbound traffic, one per availability zone in HA mode, giving us three static egress IPs by default. For payment providers that require a single whitelisted IP, we route traffic through a dedicated subnet with its own NAT Gateway. Either way, the per-node Elastic IP juggling is gone.

**Ephemeral nodes, not pets.** Every named, lovingly-maintained EC2 instance was replaced by ASG-managed nodes built from a single base AMI (Debian 12, Docker pre-installed). Two ASGs per cluster are configured: one for managers, one for workers. In practice, we currently run all nodes as managers, but the separation is there for when we need to scale worker-only nodes independently. Either way, the ASG split means scale-in events can never accidentally destroy Swarm quorum. Instance refreshes use ``MinHealthyPercentage: 110``, so a replacement node is always launched before the old one is terminated. The cluster never dips below its current count, and Raft quorum is never at risk. A termination lifecycle hook runs ``drain_node.sh`` to gracefully evacuate tasks before a node disappears, rather than letting the Swarm discover the absence on its own.

The real magic is in the node lifecycle. When an ASG launches or replaces an instance, AWS CodeDeploy runs four lifecycle hooks defined in ``appspec.yml``:

.. code-block:: yaml
   :linenos:

    hooks:
      ApplicationStop:
        - location: clusters/drain_node.sh
          timeout: 900
      BeforeInstall:
        - location: clusters/cleanup_server.sh
          timeout: 300
      AfterInstall:
        - location: clusters/swarm_node_init.sh
          timeout: 300
      ApplicationStart:
        - location: clusters/sidecars_init.sh
          timeout: 300

The first two hooks handle draining and cleanup. The interesting work is in ``swarm_node_init.sh``: it pulls Swarm join tokens from AWS Secrets Manager, validates which manager IPs are actually reachable, and either joins the existing cluster or initializes a new one if this is the first manager. It labels the node with its availability zone, syncs the updated manager IP list back to Secrets Manager, and logs into ECR. Finally, ``sidecars_init.sh`` deploys shared infrastructure services, but only on the first manager to join the cluster. Subsequent nodes don't need to run it: Swarm automatically schedules global services onto any new node, and replicated services already have their desired replica count filled.

**One deployment model.** Every service now deploys the same way: ``docker stack deploy`` triggered by GitHub Actions through a reusable workflow called ``cd.stack.yml``. The workflow SSHes into a validated manager node, pulls the compose file and secrets, runs an optional ``prepare.sh`` for migrations or pre-deploy setup, and deploys.

.. code-block:: bash
   :linenos:

    docker stack deploy -c docker-compose.yml \
        --prune --with-registry-auth --detach=false \
        $STACK_NAME

Every service is now a Swarm stack. Every stack lives in ``clusters/<cluster>/<service>/docker-compose.yml``. The pattern is identical whether you're deploying a Go microservice or the core API.


Boring in the Best Way
----------------------
Day-to-day, a deployment looks like this: a developer pushes to the relevant branch, a ``repository_dispatch`` event fires, and ``cd.stack.yml`` takes over. First, it picks a responsive manager by probing each known IP on port 2377:

.. code-block:: bash
   :linenos:

    # Manager selection: first reachable manager wins
    for IP in $MANAGER_IPS; do
        if [[ -z $(timeout 3 bash -c "echo > /dev/tcp/$IP/2377" 2>&1) ]]; then
            MANAGER_IP="$IP"
            break
        fi
    done

Once a manager is selected, the workflow SSHes in and runs ``docker stack deploy``. Then it watches:

.. code-block:: bash
   :linenos:

    # Post-deploy: 3-minute rollback watch
    MAX_WAIT=180
    ELAPSED=0
    POLL_INTERVAL=10
    while [[ $ELAPSED -lt $MAX_WAIT ]]; do
        for SERVICE in $SERVICES; do
            STATUS=$(docker service inspect $SERVICE \
                --format "{{.UpdateStatus.State}}")
            if echo "$STATUS" | grep -qE "rollback|paused"; then
                exit 1
            fi
        done
        sleep $POLL_INTERVAL
        ELAPSED=$((ELAPSED + POLL_INTERVAL))
    done

Slack gets notified. A GitHub deployment record is created. The developer never SSHes into anything.

Here's what a production service definition actually looks like. A real Compose file from one of our clusters:

.. code-block:: yaml
   :linenos:

    services:
      api_server:
        image: ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/service:main
        env_file:
          - .env
        networks:
          - swarm-ingress-overlay
          - service-network
        deploy:
          replicas: 2
          placement:
            preferences:
              - spread: node.labels.aws_az
          update_config:
            parallelism: 1
            order: start-first
            failure_action: rollback
            monitor: 30s
          rollback_config:
            failure_action: pause
            order: stop-first
            parallelism: 1
          labels:
            - "traefik.enable=true"
            - "traefik.http.routers.service.rule=Host(`production.service.internal.example.com`)"
            - "traefik.http.services.service.loadbalancer.server.port=8040"
            - "traefik.docker.lbswarm=true"
          resources:
            limits:
              memory: 512M
        logging:
          driver: fluentd
          options:
            tag: "docker.nr.{{.Name}}"
            fluentd-async: "true"

Every service connects to ``swarm-ingress-overlay`` for Traefik routing and gets a per-stack internal network for service-to-service communication. Resource limits are enforced. Replicas spread across AZs.

Logging goes through Fluent Bit (via the ``fluentd`` driver) to New Relic. Note ``fluentd-async: "true"``, because it matters more than it looks. Without it, the driver blocks synchronously: if Fluent Bit goes down, your containers can't write to stdout and will hang. One flag is the difference between "we lost some logs" and "logging took down production."

The shared sidecars, seven of them, deployed once by ``sidecars_init.sh`` when the first manager joins and then distributed by Swarm's own scheduling, handle everything the application services shouldn't have to think about:

- **Traefik** (replicated) creates the ``swarm-ingress-overlay`` network and handles ingress, routing based on deploy labels
- **Fluent Bit** (global) forwards container logs to New Relic, with config pulled from S3
- **OpenTelemetry Collector** (replicated) collects application service metrics over a dedicated ``telemetry`` network
- **cAdvisor** (global) collects Docker service and container metrics and sends them to Prometheus
- **cred-sync** (replicated, single instance) periodically refreshes ECR login credentials cluster-wide (custom-built)
- **Portainer Edge Agent** (global) connects each node to a central Portainer server for cluster management
- **cleanup** (global) prunes old Docker images to prevent disk pressure

We consolidated our previously fragmented observability stack into two primary layers.

**New Relic** handles APM, infrastructure metrics, host metrics, and container-level visibility. The infrastructure agent is installed on every node during ``swarm_node_init.sh``, and each service integrates APM by wrapping its entrypoint with the New Relic agent in its Dockerfile:

.. code-block:: dockerfile
   :linenos:

    # newrelic package installed via requirements.txt / pyproject.toml
    CMD ["newrelic-admin", "run-program", "uvicorn", "app:application", "--host", "0.0.0.0"]

In early 2026, we moved infrastructure metrics to New Relic as well. Node Exporters are still baked into the base AMI and cAdvisor still feeds Prometheus, but New Relic's infrastructure agent now handles what we used to rely on Prometheus and Grafana for. The old stack is still running as a deliberate fallback, but it's no longer what we reach for during an incident.

**Percona Monitoring and Management** owns the database layer (host metrics, query analytics, replication stats), kept separate from application observability so database issues don't get lost in application noise.

Secrets live in AWS Secrets Manager, fetched at deploy time, never stored on disk, never committed to the repo. Each cluster has a ``cluster_info.json`` that points to its Secrets Manager resource, S3 config bucket, and Traefik subdomain marker.


What We Learned the Hard Way
----------------------------
**Sidecars are the unglamorous backbone.** Nobody writes blog posts about their ECR credential refresh job. But we had to build ``cred-sync`` because Docker Swarm has a subtle gap: it stores registry credentials in the Raft log at deploy time and never refreshes them. ECR tokens expire after 12 hours by default. So when a scale-out or rebalance places a container on a node without the image cached, the pull fails with expired credentials. ``cred-sync`` re-authenticates periodically before tokens expire.

Off-the-shelf solutions exist, but the logic is simple enough that we chose to build our own rather than take a dependency on someone else's implementation for something this critical.

The same principle applies to Fluent Bit, cAdvisor, and the image cleanup service. Invest in the boring infrastructure that keeps the cluster healthy, not just the application services that run on it.

**Security improvements often pay for themselves.** Moving to private subnets wasn't just a security win. It also reduced our cloud spend and removed operational overhead around IP management. When someone tells you "we can't afford to fix the security posture," run the numbers. You might find it's the insecure setup that's expensive.

**Consistency matters more than the orchestrator debate.** Docker Swarm versus Kubernetes is a fun argument at conferences. In practice, the thing that transformed our operations was having a single deployment model for every service. Swarm made that easy because the tooling was already familiar, but the real win was the consistency itself: a single ``cd.stack.yml`` workflow, a single compose file convention, a single set of sidecars. Consistency compounds.

**Plan for quorum loss before it happens.** Swarm's Raft consensus means losing a majority of managers takes down the control plane. We learned to automate the recovery path inside ``swarm_node_init.sh``. If a node detects a leaderless cluster, it attempts to recover automatically:

.. code-block:: bash
   :linenos:

    if [[ -n $(docker node ls 2>&1 | grep "The swarm does not have a leader.") ]]; then
        docker swarm init --force-new-cluster \
            --advertise-addr $HOST_IP \
            --default-addr-pool 10.100.0.0/16
    fi

Prevention is better. The ASG separation, ``MinHealthyPercentage: 110``, and drain hooks make quorum loss unlikely. But having automated recovery in the node init path means a cluster can heal itself without someone being paged.

**Make your clusters machine-readable from day one.** One of the best decisions we made early was introducing a ``cluster_info.json`` schema for every cluster. Having a machine-readable description of each cluster's resources made automation dramatically easier, and it kept paying dividends as we scaled to seven clusters and beyond.


The Dust Settles
----------------
Docker Swarm is not the right choice for everyone. If you're running hundreds of services with complex service mesh requirements, you need Kubernetes. If you're a three-person startup with two containers, you need a PaaS.

But there's a wide band of teams in between, teams with ten to fifty services, a small platform engineering function, and a strong preference for tools that compose well with what they already know. For those teams, Swarm is worth a serious look. Not because it's trendy (it emphatically is not), but because it's the rare piece of infrastructure software that does exactly what it promises and then gets out of the way.

Stability, in production, means your infrastructure is boring enough that you can focus on the product. It requires the right structure, the right signals, and enough automation that no single person's knowledge is load-bearing. We're not quite there yet. Our alert coverage still has gaps, and a few services could use tighter resource limits. But we sleep better than we used to, and that's the only metric that matters.


.. _references:

References
----------
 1. `Docker Swarm Mode Overview <https://docs.docker.com/engine/swarm/>`_
 2. `Docker Swarm Protocols and Ports <https://docs.docker.com/engine/swarm/swarm-tutorial/#open-protocols-and-ports-between-the-hosts>`_
 3. `Recovering from Swarm Quorum Loss <https://docs.docker.com/engine/swarm/admin_guide/#recover-from-losing-the-quorum>`_
 4. `Swarm Does Not Refresh Registry Credentials (moby #31063) <https://github.com/moby/moby/issues/31063>`_
 5. `Integrating AWS CodeDeploy with EC2 Auto Scaling <https://docs.aws.amazon.com/codedeploy/latest/userguide/integrations-aws-auto-scaling.html>`_
 6. `CodeDeploy AppSpec File Reference <https://docs.aws.amazon.com/codedeploy/latest/userguide/reference-appspec-file.html>`_
 7. `New Relic Python Agent: newrelic-admin run-program <https://docs.newrelic.com/docs/apm/agents/python-agent/installation/python-agent-admin-script-advanced-usage/#run-program>`_
