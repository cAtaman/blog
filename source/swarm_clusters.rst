.. toctree::


=============================================================
From Buzz to Balance: Docker Swarm Orchestration Without Tears
=============================================================


Something Isn't Right Here
--------------------------
You know the feeling. You SSH into a production server at 11pm because a deploy failed, and you're greeted by a process you didn't start, on a box you didn't provision, running a version of the code you're not sure anyone intended to ship. You fix it. You go to bed. You do it again next Thursday.

That was us. Not in some dramatic, everything-is-on-fire way -- more like a slow accumulation of workarounds that had quietly become the system. We had infrastructure that *worked*, technically. But it didn't work the way infrastructure should: predictably, boringly, without someone's personal knowledge of which server does what.

This is the story of how we moved from that to something we actually trust. Not by chasing the shiniest orchestration tool on the market, but by picking the one that fit -- Docker Swarm -- and building the automation around it that made the difference.


Three Deployment Models Walk Into a VPC
----------------------------------------
Our old setup was less "architecture" and more "archaeology." Over the years, we'd accumulated three distinct ways of deploying services, each born from a different era of the platform's growth.

**Model 1: Raw code on bare servers.** The core API -- our oldest and largest service -- ran as raw Python on dedicated EC2 instances. Django behind uWSGI behind Nginx, deployed by SSHing in and running a script like this on each app server:

.. code-block:: bash

    # The "deployment pipeline" for the core API app server
    cd /opt/projects/app
    git checkout master -f && git pull
    git submodule update --init --remote
    source venv3.11/bin/activate && poetry install
    python manage.py migrate && python manage.py collectstatic --no-input
    kill -HUP $(cat /tmp/app.pid)   # graceful uWSGI reload

Three app servers behind an ELB, one task server running RabbitMQ and Celery workers managed by Supervisor (same git-pull ritual, but ending with ``supervisorctl restart all``). Capacity changes meant provisioning a new instance by hand.

**Model 2: Standalone Docker containers.** Newer microservices ran as Docker containers on a single host -- Traefik in front, ``docker-compose up -d`` to deploy. No orchestration, no health checks beyond what Traefik provided, no resource limits.

**Model 3: The accidental Swarm cluster.** Our SOA server started as a Docker Compose host and gradually became an ad-hoc Swarm cluster as we bolted on more services -- less organised colony, more bees in a shoebox. Staging and production workloads shared the same nodes. A misbehaving staging service could -- and occasionally did -- starve a production one.

All of this ran on **public subnets**. Some instances had public IPv4 addresses. We were spending roughly $2,500 a year on public IPs alone, and every microservice node needed an Elastic IP for payment provider whitelisting. Observability was split across a self-hosted Elasticsearch cluster for logs, an Elastic Cloud instance for APM and traces, plus a separate Prometheus and Grafana stack for infrastructure metrics. Coverage was inconsistent. Incidents on the SOA cluster were routinely discovered through customer reports, not alerts.

It worked. Until it didn't scale. Not in the "we need 10x throughput" sense, but in the "we can't onboard another service without someone getting paged" sense.


The Elephant in the Room
------------------------
Yes, we picked Docker Swarm. In 2024. We've heard the jokes.

Here's the thing: Kubernetes is an extraordinary piece of engineering. It is also, for a team our size managing the number of services we run, an extraordinary amount of operational overhead. We didn't need service meshes, custom resource definitions, or a dedicated platform team to manage the orchestrator itself. We needed containers, rolling updates, service discovery, and overlay networking. Swarm gives us all of that with a tool our engineers already know: the Compose file.

A Swarm compose file *is* a Docker Compose file with a ``deploy`` key. There's no new DSL to learn, no Helm charts to template, no YAML-in-YAML to debug at 2am. Our developers write the same ``docker-compose.yml`` they use locally, add deployment constraints and resource limits, and that's the production manifest.

.. code-block:: yaml

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
        parallelism: 1
        order: stop-first
      restart_policy:
        condition: on-failure
        delay: 2s
        window: 30s
      resources:
        limits:
          memory: 164M

``start-first`` gives us zero-downtime rolling updates. ``spread: node.labels.aws_az`` distributes replicas across availability zones. ``failure_action: rollback`` with a 30-second ``monitor`` window means a bad deploy automatically rolls back — and if the rollback itself fails, it pauses so a human can investigate instead of looping. Could Kubernetes do more? Absolutely. Do we need more? Not today.

We're not ideological about this. If we outgrow Swarm, we'll migrate. But "you might need Kubernetes someday" is not a reason to adopt it now -- not when the operational tax is real and the benefits are theoretical for our workload.


Rewiring the Foundation
-----------------------
The migration wasn't a big bang. It was a series of deliberate changes across networking, compute, deployment, and observability -- each one load-bearing enough that we couldn't afford to rush.

**Private subnets, finally.** We moved all compute off public subnets and into private ones (``10.0.6.0/24`` through ``10.0.8.0/24``). Services that don't need internet exposure are no longer reachable from the internet, full stop. NAT Gateways handle outbound traffic -- one per availability zone in HA mode, giving us three static egress IPs by default. For payment providers that require a single whitelisted IP, we route traffic through a dedicated subnet with its own NAT Gateway. Either way, the per-node Elastic IP juggling is gone.

**Ephemeral nodes, not pets.** Every named, lovingly-maintained EC2 instance was replaced by ASG-managed nodes built from a single base AMI (Debian 12, Docker pre-installed). Two ASGs per cluster: one for managers, one for workers. This separation means scale-in events can never accidentally destroy Swarm quorum. Instance refreshes use ``MinHealthyPercentage: 110``, so a replacement node is always launched before the old one is terminated — the cluster never dips below its current count, and Raft quorum is never at risk. A termination lifecycle hook runs ``drain_node.sh`` to gracefully evacuate tasks before a node disappears, rather than letting the Swarm discover the absence on its own.

The real magic is in the node lifecycle. When an ASG launches or replaces an instance, AWS CodeDeploy runs four lifecycle hooks defined in ``appspec.yml``:

.. code-block:: yaml

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

The first two hooks handle draining and cleanup. The interesting work is in ``swarm_node_init.sh``: it pulls Swarm join tokens from AWS Secrets Manager, validates which manager IPs are actually reachable, and either joins the existing cluster or initialises a new one. It labels the node with its availability zone, syncs the updated manager IP list back to Secrets Manager, and logs into ECR. Finally, ``sidecars_init.sh`` deploys shared infrastructure services -- but only on the first manager to join the cluster. Subsequent nodes don't need to run it: Swarm automatically schedules global services onto any new node, and replicated services already have their desired replica count filled.

**One deployment model.** The three old approaches collapsed into a single pattern: ``docker stack deploy`` triggered by GitHub Actions through a reusable workflow called ``cd.stack.yml``. The workflow SSHes into a validated manager node, pulls the compose file and secrets, runs an optional ``prepare.sh`` for migrations or pre-deploy setup, and deploys. (``prepare.sh`` is not the same as the ``start.sh`` scripts sidecars use during node init — it's fetched from the repo at deploy time and only runs during deployments.)

.. code-block:: bash

    docker stack deploy -c docker-compose.yml \
        --prune --with-registry-auth --detach=false \
        $STACK_NAME

Every service is now a Swarm stack. Every stack lives in ``clusters/<cluster>/<service>/docker-compose.yml``. The pattern is identical whether you're deploying a Go microservice or the core API.


Boring in the Best Way
----------------------
Day-to-day, a deployment looks like this: a developer pushes to the relevant branch, a ``repository_dispatch`` event fires, and ``cd.stack.yml`` takes over. First, it picks a responsive manager by probing each known IP on port 2377:

.. code-block:: bash

    # Manager selection — first reachable manager wins
    for IP in $MANAGER_IPS; do
        if [[ -z $(timeout 3 bash -c "echo > /dev/tcp/$IP/2377" 2>&1) ]]; then
            MANAGER_IP="$IP"
            break
        fi
    done

Once a manager is selected, the workflow SSHes in and runs ``docker stack deploy``. Then it watches:

.. code-block:: bash

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

Here's what a production service definition actually looks like -- a real Compose file from one of our clusters:

.. code-block:: yaml

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
              memory: 164M
        logging:
          driver: fluentd
          options:
            tag: "docker.nr.{{.Name}}"
            fluentd-async: "true"

Every service connects to ``swarm-ingress-overlay`` for Traefik routing and gets a per-stack internal network for service-to-service communication. Resource limits are enforced. Replicas spread across AZs.

Logging goes through Fluent Bit (via the ``fluentd`` driver) to New Relic. Note ``fluentd-async: "true"`` — it matters more than it looks. Without it, the driver blocks synchronously: if Fluent Bit goes down, your containers can't write to stdout and will hang. One flag is the difference between "we lost some logs" and "logging took down production."

The shared sidecars -- seven of them, deployed automatically via ``sidecars_init.sh`` -- handle everything the application services shouldn't have to think about:

- **Traefik** (replicated) creates the ``swarm-ingress-overlay`` network and handles ingress, routing based on deploy labels
- **Fluent Bit** (global) forwards container logs to New Relic, with config pulled from S3
- **OpenTelemetry Collector** (replicated) handles distributed tracing over a dedicated ``telemetry`` network
- **cAdvisor** (global) collects Docker service and container metrics and sends them to Prometheus
- **cred-sync** (replicated, single instance) periodically refreshes ECR login credentials cluster-wide (custom-built)
- **Portainer Edge Agent** (global) connects each node to a central Portainer server for cluster management
- **cleanup** (global) prunes old Docker images to prevent disk pressure

Observability went from four fragmented systems (self-hosted Elasticsearch for logs, Elastic Cloud for APM and traces, a separate Prometheus/Grafana stack for infrastructure metrics, and MetricBeat for application metrics) to a cleaner split across three purpose-built layers. **New Relic** handles APM, host metrics, and container-level visibility — the infrastructure agent is installed on every node during ``swarm_node_init.sh``, and each service integrates APM by wrapping its entrypoint with the New Relic agent in its Dockerfile:

.. code-block:: dockerfile

    # newrelic package installed via requirements.txt / pyproject.toml
    CMD ["newrelic-admin", "run-program", "uvicorn", "app:application", "--host", "0.0.0.0"]

**Prometheus and Grafana** handle infrastructure metrics. Node Exporters are baked into the base AMI with their port exposed, so Prometheus discovers and scrapes every node automatically. cAdvisor feeds Docker service and container metrics into the same Prometheus instance. **Percona Monitoring and Management** owns the database layer — host metrics, query analytics, replication stats — kept separate from application observability so database issues don't get lost in application noise.

Secrets live in AWS Secrets Manager, fetched at deploy time -- never stored on disk, never committed to the repo. Each cluster has a ``cluster_info.json`` that points to its Secrets Manager resource, S3 config bucket, and Traefik subdomain marker.


What We Learned the Hard Way
----------------------------
**Sidecars are the unglamorous backbone.** Nobody writes blog posts about their ECR credential refresh job. But we had to build ``cred-sync`` because Docker Swarm has a subtle gap: it stores registry credentials in the Raft log at deploy time and never refreshes them. ECR tokens expire after 12 hours by default. So when a scale-out or rebalance places a container on a node without the image cached, the pull fails with expired credentials. ``cred-sync`` re-authenticates periodically before tokens expire.

Off-the-shelf solutions exist, but the logic is simple enough that we chose to build our own rather than take a dependency on someone else's implementation for something this critical.

The same principle applies to Fluent Bit, cAdvisor, and the image cleanup service. Invest in the boring infrastructure that keeps the cluster healthy, not just the application services that run on it.

**Security improvements often pay for themselves.** Moving to private subnets wasn't just a security win -- it eliminated $2,500/year in public IPv4 costs and removed the operational overhead of managing per-node Elastic IPs for payment provider whitelisting. When someone tells you "we can't afford to fix the security posture," run the numbers. You might find it's the insecure setup that's expensive.

**The deployment model matters more than the orchestrator.** Swarm versus Kubernetes is a fun argument at conferences. In practice, the thing that transformed our operations wasn't the choice of orchestrator -- it was going from three inconsistent deployment models to one. A single ``cd.stack.yml`` workflow, a single compose file convention, a single set of sidecars. Consistency compounds.

**Plan for quorum loss before it happens.** Swarm's Raft consensus means losing a majority of managers takes down the control plane. We learned to automate the recovery path inside ``swarm_node_init.sh`` — if a node detects a leaderless cluster, it attempts to recover automatically:

.. code-block:: bash

    if [[ -n $(docker node ls 2>&1 | grep "The swarm does not have a leader.") ]]; then
        docker swarm init --force-new-cluster \
            --advertise-addr $HOST_IP \
            --default-addr-pool 10.100.0.0/16
    fi

Prevention is better — the ASG separation, ``MinHealthyPercentage: 110``, and drain hooks make quorum loss unlikely — but having automated recovery in the node init path means a cluster can heal itself without someone being paged.

If we could go back, we'd invest in the ``cluster_info.json`` schema earlier. Having a machine-readable description of each cluster's resources made automation dramatically easier -- we just didn't realise how much until we had five clusters to manage.


The Dust Settles
----------------
Docker Swarm is not the right choice for everyone. If you're running hundreds of services with complex service mesh requirements, you need Kubernetes. If you're a three-person startup with two containers, you need a PaaS.

But there's a wide band of teams in between -- teams with ten to fifty services, a small platform engineering function, and a strong preference for tools that compose well with what they already know. For those teams, Swarm is worth a serious look. Not because it's trendy (it emphatically is not), but because it's the rare piece of infrastructure software that does exactly what it promises and then gets out of the way.

Balance, in production, means your infrastructure is boring enough that you can focus on the product. A well-run swarm doesn't need a beekeeper hovering over it every hour -- it needs the right structure, the right signals, and enough automation that no single person's knowledge is load-bearing. We're not quite there yet -- our alert coverage still has gaps, and a few services could use tighter resource limits. But the workers are doing their thing, and we sleep better than we used to.
