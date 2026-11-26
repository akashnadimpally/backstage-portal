from dataclasses import dataclass


@dataclass
class AgentResult:
    agent: str
    content: str


class ResearchAgent:
    async def run(self, task: str) -> AgentResult:
        return AgentResult("research", f"Research task received: {task}")


class ReviewerAgent:
    async def run(self, task: str, research: AgentResult) -> AgentResult:
        return AgentResult("reviewer", f"Review the research before responding: {research.content}")


async def run_workflow(task: str) -> list[AgentResult]:
    research = await ResearchAgent().run(task)
    review = await ReviewerAgent().run(task, research)
    return [research, review]