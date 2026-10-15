# siedler-agent

Runs [OpenCode](https://opencode.ai) on GitHub's public runners to work on private repositories.
Triggered by `/opencode <task>` comments from the owner only. Results go exclusively as branches /
draft PRs to the private repos; this repo contains only the workflow, and comments contain only short summaries.
