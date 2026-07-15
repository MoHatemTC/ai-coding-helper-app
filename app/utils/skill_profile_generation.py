"""Utility functions for skill profile generation."""

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from app.schemas.skill_profile import SkillProfile

load_dotenv()


prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You are analyzing a developer's code review findings to maintain "
                "an up-to-date skill profile. Given their current profile and a list "
                "of new findings, produce an updated skill profile.\n\n"
                "Rules:\n"
                "- Only raise skill_level if findings show consistent competence, "
                "not from a single clean finding.\n"
                "- Add a weakness only for a topic with a recurring pattern of "
                "issues, not a one-off mistake. If a topic already has a weakness "
                "and new findings still show the issue, keep or refine it; if the "
                "findings show it's resolved, drop it.\n"
                "- Merge all_searched_topics: keep existing topics and add any new "
                "ones found in this batch, no duplicates."
            ),
        ),
        ("human", ("Current skill profile:\n{current_profile}\n\nNew findings from this session:\n{findings}")),
    ]
)


async def generate_skill_profile(
    current_profile: SkillProfile | None,
    findings: list[dict],
) -> SkillProfile:
    """Use LLM to generate/update skill profile from findings."""
    current_profile_text = (
        current_profile.model_dump_json()
        if current_profile
        else "No profile yet — this is the user's first analyzed session."
    )

    llm = ChatGroq(model="FW-Kimi-K2.6", temperature=0)
    structured_llm = llm.with_structured_output(SkillProfile)
    chain = prompt | structured_llm

    result = await chain.ainvoke(
        {
            "current_profile": current_profile_text,
            "findings": findings,
        }
    )
    if not isinstance(result, SkillProfile):
        raise TypeError(f"Expected SkillProfile from structured output, got {type(result)}")
    return result
