.. toctree::


=====================================================================
Streamlining Developer Workflows: A case study on Deployment Previews
=====================================================================


Introduction
------------
In a collaborative software development environment, managing multiple developers working on a shared codebase can present challenges in terms of efficiency, consistency, and collaboration. Streamlining developer workflows and ensuring efficient communication and coordination are essential for success within a team. In this blog post, we will delve into how we successfully enhanced collaboration in our shared codebases, providing insights into the challenges, common workflows, and solutions we have implemented.


Multiple Developers, One Codebase
---------------------------------
When numerous developers contribute code to a shared codebase, a multitude of challenges arise impacting the quality and speed of software development. These include conflicts arising from simultaneous changes, deployment issues, and potential communication gaps.It is essential to establish clear guidelines, communication channels and structured workflows to tackle these problems and ensure smooth collaboration.

An organization must invest in tools and resources that will facilitate collaboration among developers and make it easier to track changes. Version control tools like Git, and code-sharing platforms like GitHub, help teams track and record changes made on codebases, however, the proper use of this tools is what determines its effectiveness.

.. note::
    For those unfamiliar with the concept, a codebase refers to the collection of source code files used to build a software application. Git, a widely used version control system, facilitates the simultaneous editing of the same codebase by multiple developers, managing changes and revisions effectively. You can read more on it with the Pro Git Book :ref:`[1] <references>`


Developer Workflows
-------------------
Developer workflows define the series of steps and processes involved in creating, testing, reviewing, and deploying code changes. A well-defined workflow promotes transparency, accountability, and seamless coordination among team members. Implementing best practices for developer workflows can lead to faster delivery of features, improved code quality, and better collaboration.

In a shared codebase environment, the choice of developer workflows can significantly impact collaboration, code quality, and the integration of changes. Several common workflows are utilized by development teams to streamline collaboration:

Trunk-Based Development
^^^^^^^^^^^^^^^^^^^^^^^
Trunk-Based Development emphasizes the continuous integration of changes into the main codebase. This approach involves developers committing their changes directly to the main branch, promoting frequent integration and rapid feedback. While it offers benefits in terms of immediate code integration, Trunk-Based Development requires robust testing and continuous monitoring to maintain code stability.

Feature Branch Workflow
^^^^^^^^^^^^^^^^^^^^^^^
The Feature Branch Workflow is a popular approach used by development teams to manage changes in a shared codebase. In this workflow, developers create separate branches for each feature or task they are working on. By isolating development efforts in these branches, developers can work on new features without interfering with the main codebase. This approach promotes seamless code integration and facilitates effective code reviews and testing before changes are merged into the main branch.

Git Flow
^^^^^^^^
Another widely used workflow is Git Flow, which defines a branching model for managing changes across different stages of software development. This approach prescribes the use of specific branches for features, releases, and hotfixes, providing a clear structure for code integration and release management. Git Flow serves as a comprehensive framework for orchestrating collaboration and ensuring a stable codebase throughout the development lifecycle. You can go more in-depth on this workflow by checking out the Git Flow article :ref:`[2] <references>`.

This is anything but an exhaustive list. You can read more on other possible workflows on this article about Branching Patterns :ref:`[3] <references>`.


How We Solved This at Cowrywise
-------------------------------
The choice of a developer workflow depends on the specific needs and dynamics of the development team and the nature of the projects undertaken. At Cowrywise, we have adopted the **Feature Branch Workflow** as our primary development process, tailoring our approach to the unique requirements of our team and projects. We also implemented several strategies to optimize this workflows within our teams. Here are some key approaches we adopted:

Leveraging Feature Flags for Flexibility
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Introducing new features into a shared codebase environment can be a complex process, particularly when it comes to managing feature rollouts, conducting A/B testing, and enabling or disabling features based on user feedback. Feature flags, also known as feature toggles, provide a powerful mechanism to address these challenges. At Cowrywise, we have integrated feature flags into our codebase to grant us the flexibility to enable or disable specific features independent of code deployment.

This capability has proven invaluable in orchestrating gradual feature rollouts, conducting A/B testing, and seamlessly controlling feature access for different user segments. With feature flags, we can release new functionality to a subset of users, gather feedback, and make informed decisions about feature activation and refinement. Furthermore, feature flags offer a mechanism for instant rollback by simply toggling off a feature flag, providing us with a safety net in case of unexpected issues following a feature rollout.

Implementing Deployment Previews for Validation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
This was the game-changer for us and I'll be expanding on this in the next section.

Testing and validating changes before integrating them into the main codebase are essential to ensure the stability and functionality of the application. We have implemented deploy previews for feature branches which is activated when needed. As developers work on new features in their respective branches, the changes are automatically deployed to a staging environment, allowing developers and stakeholders to preview and interact with the new features in a production-like setting. This approach enables thorough testing, validation, and feedback collection, contributing to greater confidence in the quality and functionality of the changes before they are merged into the main codebase.

This is especially useful when changes affects more than one team; for example, while the backend team works on a feature that affects the mobile, the mobile team would have a preview URL for use. Also the mobile team can build test applications with this to enable manual testing by non-technical persons.


Deployment Previews: Our Implementation Journey
-----------------------------------------------
So, at this point, we knew what we needed -- a way to get a deployment of feature branches that mirrors our staging environment. Making this happen however was quite the journey. Being fans of controlling our entire deployment stack, using completely external solutions was not high on our list.

First Draft
^^^^^^^^^^^
The first draft for this made use of Docker images, Github Actions, a dedicated EC2 box and tunneling tools like `localtunnel <https://github.com/localtunnel/localtunnel>`_ and `ngrok <https://ngrok.com/use-cases/ingress-for-dev-test-environments>`_.

First, on GitHub, we created a custom label ``branch-deploy``, then we created a GitHub Actions workflow that would be triggered when a pull request (or PR) with this label is opened or updated. When a feature branch is ready, we open a PR for it to the main development branch (develop). If a deployment preview is needed for the feature we attach the ``branch-deploy`` label to the PR.

After the normal Continuous Integration (CI) tests (also a GitHub Actions workflow) runs and passes, the CI job calls the branch-deploy workflow using the  ``workflow_call`` trigger. You can read more about that on the `GitHub doc <https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#workflow_call>`_.

The branch-deploy workflow logs into the provisioned EC2 server and does a few simple things:

 #. Builds a Docker image using the codebase's Dockerfile.
 #. Deploys the image using `Docker Compose <https://docs.docker.com/compose/>`_.
 #. Creates a secure web tunnel from this server to the internet using either localtunnel or ngrok.

There were a few problems with this approach.

First, and most annoyingly, the deployments have no state as the databases was destroyed and recreated on each deployment (or each push to the deployed branch).

Second, the tunneling tools in use were not perfect; localtunnel (free infamously becomes unreachable at random while for ngrok, we were limited to a single tunnel link on the free plan. We had no intentions of paying for this service.


Second Draft
^^^^^^^^^^^^
We made some updates to the first draft to fix a few pain points.

We created a volume for the database containers on deployments. This volume was namespaced to the head branch of the PR that created it. This way, all updates to the PR and even new PRs on that branch would make use of this volume. Data for the feature would be persisted. This worked perfectly.

As for tunneling tools, we started testing out alternatives. We tried hosting our own `localtunnel <https://github.com/localtunnel/localtunnel>`_ instance (it's an open-source project) however we ran into some blockers there and were unwilling to allocated precious developer hours to fix a seemingly unmaintained project. Then we checked for alternative tools and found `serveo.net <https://serveo.net/>`_. Also, for good measure, we had all these services in active use. Our deployment job was set up to try each on in succession in this order: localtunnel, serveo, ngrok till one succeeds.
Also, both serveo.net and localtunnel allowed setting up a dedicated subdomain for deployments so we set that up. This meant each update would get the same deployment URL. (On ngrok this is a paid feature so we didn't bother with it).
This worked well enough for a while.

Now all of this was starting to feel really good but it didn't last.

The tunneling services still had issues at times. localtunnel, after a successful deployment would just sometimes refuse connections. serveo would sometimes just not work, which meant a lot of fallbacks to ngrok and thus a lot of changes to deployment URLs. This was a real frustration to the mobile and web teams.

Then localtunnel and serveo.net came with an understandable but frustrating-for-us security feature where each unique user connecting to the tunnel URL had to be verified. They would redirect the requester to a web page where they are to enter the host IP address. This was to make sure the users know they were connecting to a tunneled link. But we were using this with APIs and that was unacceptable to us.

Also, the single EC2 box would sometimes run out of resources and there was a cap to the number of deployments we could make. This was mostly due to the task server we have bundled with the app. We had a few task worker instances for processing asynchronous requests.


Radical Third Draft (Final form)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
We had the second draft running for little while as, again, developer hours was not in abundance.

Then an opportunity presented itself. We had just setup a `Docker Swarm <https://docs.docker.com/engine/swarm/>`_ cluster with multiple compute nodes. We updated our branch-deploy workflow to deploy the feature branches as stacks in the swarm cluster. We also reduced the number of task workers. This way, our resource problem was solved.

Then to solve our tunneling issue, we finally went fully internal. We made use of `Traefik <https://doc.traefik.io/traefik/>`_, an open source edge router to route requests from the internet, through an internal tunneling subdomain ``"*.tunnels..."`` to the deployed Docker Swarm stack. This was not as easy or as straight-forward as it sounds.


In a nutshell
-------------
By embracing the Feature Branch Workflow, implementing deploy previews, and leveraging feature flags, Cowrywise has enhanced collaboration and efficiency in our shared codebase environment. These measures have not only improved the quality and stability of our software products but have also fostered a culture of seamless collaboration and rapid iteration within our development team. We cut down time-to-deployment of major features down from several days to under one day.

As software development continues to evolve, the emphasis on streamlined workflows and collaboration will remain foundational to delivering innovative, high-quality products in a collaborative environment. Are you ready to optimize your developer workflows and enhance collaboration in your team's shared codebase?

Feel free to share your thoughts or any specific areas you would like to explore further.

**Happy coding and collaborating!**


.. _references:

References
----------

 1. `Pro Git Book <https://git-scm.com/book/en/v2/Getting-Started-About-Version-Control>`_
 2. `Git Flow <https://nvie.com/posts/a-successful-git-branching-model/>`_
 3. `Branching Patterns <https://martinfowler.com/articles/branching-patterns.html>`_
