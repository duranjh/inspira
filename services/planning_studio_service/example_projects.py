"""Hand-authored example project seeds for the Inspira onboarding flow.

New users can pick a domain they're curious about and land on a canvas
already populated with topics, sample decisions, and sample Q&A turns.
This lets them poke around something real before committing to their own
project — the goal is to *teach by showing*, not by explaining.

Wire-up guide for api.py
------------------------
Import at the top of api.py:

    from .example_projects import (
        EXAMPLE_PROJECTS,
        ExampleProjectBody,
        instantiate_example_project,
    )

Add two routes (after the templates section, around line 2212):

    @app.get("/api/v2/examples")
    async def list_examples():
        return {
            "examples": [
                {
                    "slug": s.slug,
                    "display_name": s.display_name,
                    "one_liner": s.one_liner,
                    "topic_count": len(s.topics),
                }
                for s in EXAMPLE_PROJECTS
            ]
        }

    @app.post("/api/v2/projects/from-example")
    async def create_project_from_example(
        body: ExampleProjectBody,
        user: Annotated[dict, Depends(current_user)],
    ):
        if user.get("is_system"):
            raise HTTPException(status_code=403, detail={"error": "auth_required"})
        try:
            project = instantiate_example_project(
                _store, user_id=user["user_id"], slug=body.slug,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": str(exc)},
            ) from exc
        topics = _store.list_topics(project_id=project["project_id"])
        return {"project": project, "topics": topics}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from .store import PlanningStudioStore


# ---------------------------------------------------------------------------
# Seed dataclasses (frozen + slots so they're cheap to pass around and safe
# to treat as constants).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExampleTurnSeed:
    planner_question: str
    why_this_matters: str
    user_answer: str


@dataclass(frozen=True, slots=True)
class ExampleDecisionSeed:
    statement: str
    rationale: str | None


@dataclass(frozen=True, slots=True)
class ExampleTopicSeed:
    title: str
    icon: str
    decisions: list[ExampleDecisionSeed]
    turns: list[ExampleTurnSeed]


@dataclass(frozen=True, slots=True)
class ExampleProjectSeed:
    slug: str           # "novel", "startup", etc.
    display_name: str   # "A literary novel"
    one_liner: str      # Short description shown on the picker card
    domain: str         # Maps to kickoff domain enum
    topics: list[ExampleTopicSeed]


# ---------------------------------------------------------------------------
# Pydantic body model (module scope — required by api.py wiring guide)
# ---------------------------------------------------------------------------


class ExampleProjectBody(BaseModel):
    slug: str


# ---------------------------------------------------------------------------
# The six hand-authored seeds
# ---------------------------------------------------------------------------


EXAMPLE_PROJECTS: list[ExampleProjectSeed] = [

    # ------------------------------------------------------------------
    # 1. Literary novel
    # ------------------------------------------------------------------
    ExampleProjectSeed(
        slug="novel",
        display_name="A literary novel",
        one_liner="Two sisters returning to their childhood home after their mother's death.",
        domain="creative",
        topics=[
            ExampleTopicSeed(
                title="Premise",
                icon="📖",
                decisions=[
                    ExampleDecisionSeed(
                        statement="The story opens on the day of the funeral, not the death itself.",
                        rationale="Avoids a melodramatic opener. The sisters' tension will be richer if the reader meets them when they're already together and already pretending to cope.",
                    ),
                    ExampleDecisionSeed(
                        statement="The house is being sold at the end of the summer — the sisters have a deadline.",
                        rationale="Gives the novel a built-in ticking clock without manufacturing an external threat.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What is the central question your reader should be sitting with at the end of chapter one?",
                        why_this_matters="A strong hook question focuses every scene-level decision for the opening act.",
                        user_answer="Whether the older sister actually resents the younger one for leaving, or whether she's been performing resentment so long she can't tell anymore.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Main characters",
                icon="👤",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Older sister (Mara) stayed; younger sister (Jo) left for London twelve years ago.",
                        rationale="Classic push-pull — the one who stayed carries the weight, the one who left carries the guilt.",
                    ),
                    ExampleDecisionSeed(
                        statement="Neither sister is the villain.",
                        rationale="The reader should finish each chapter understanding both of them, even when they're awful to each other.",
                    ),
                    ExampleDecisionSeed(
                        statement="A neighbor who knew the mother well will serve as an unreliable chorus figure.",
                        rationale=None,
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What does Mara want from this week that she won't admit out loud?",
                        why_this_matters="Hidden wants drive subtext. The gap between what a character says and wants is where your best dialogue lives.",
                        user_answer="She wants Jo to say 'I should have come home sooner.' She's been rehearsing how she'd respond for years.",
                    ),
                    ExampleTurnSeed(
                        planner_question="What is the one thing Jo genuinely misunderstands about Mara — not maliciously, just wrongly?",
                        why_this_matters="Misreadings between close characters are more interesting than outright lies.",
                        user_answer="Jo thinks Mara stayed out of devotion. Mara stayed because she was afraid of exactly what Jo did — leaving and becoming someone their mother didn't recognize.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Setting",
                icon="🏠",
                decisions=[
                    ExampleDecisionSeed(
                        statement="West Cork, Ireland. Late August.",
                        rationale="The light is specific and melancholy. The landscape does emotional work without the writer spelling it out.",
                    ),
                    ExampleDecisionSeed(
                        statement="The house smells of damp and rosemary — the mother's kitchen garden is still running.",
                        rationale="Sensory anchors ground the reader and give Mara something to do with her hands in scenes where she won't talk.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="Which room in the house is avoided by both sisters for different reasons?",
                        why_this_matters="Physical avoidance is a form of character revelation — the room becomes a symbol you can earn late in the novel.",
                        user_answer="The mother's bedroom. Mara won't touch it because clearing it means the sale is real. Jo won't enter it because the last conversation she had with her mother happened there.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Plot beats",
                icon="📍",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Act one ends with Jo finding letters the mother wrote but never sent.",
                        rationale="Needs to reframe everything the reader assumed about the mother without making her a different character — just a more complicated one.",
                    ),
                    ExampleDecisionSeed(
                        statement="No flashbacks before page 80.",
                        rationale="Let the present tense earn the reader's investment before asking them to travel back.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Themes",
                icon="🌿",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Primary theme: inheritance — what we keep, what we discard, what keeps us.",
                        rationale=None,
                    ),
                    ExampleDecisionSeed(
                        statement="Secondary theme: the stories families tell themselves to stay coherent.",
                        rationale="Every family has a myth. This one's myth is that the mother was fine with how things turned out.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Voice",
                icon="✍️",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Third-person limited, alternating between the two sisters by chapter.",
                        rationale="Allows the reader to see each sister be wrong about the other. First person would collapse the distance too early.",
                    ),
                    ExampleDecisionSeed(
                        statement="Sentences run long when a character is avoiding something; short when they've decided.",
                        rationale="Let the prose style mirror interiority without interior monologue spelling it out.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="Name a novelist whose sentence rhythms you'd like this book to borrow from.",
                        why_this_matters="A clear stylistic reference helps you calibrate during revision — 'would this sentence pass muster in that writer's register?'",
                        user_answer="Tessa Hadley. The way she writes about domestic spaces as if they're alive. Also some of Anne Enright's willingness to sit inside a character's pettiness without excusing it.",
                    ),
                ],
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # 2. B2B SaaS startup
    # ------------------------------------------------------------------
    ExampleProjectSeed(
        slug="startup",
        display_name="A B2B SaaS startup",
        one_liner="Scheduling and recall management software for small dental practices.",
        domain="business",
        topics=[
            ExampleTopicSeed(
                title="Customer + problem",
                icon="🦷",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Primary customer: independent dental practices with 1–3 chairs.",
                        rationale="DSO-owned practices have their own procurement and vendor lock-in. Solo practices make faster decisions and are hungrier for tools that actually fit them.",
                    ),
                    ExampleDecisionSeed(
                        statement="Core problem: no-show rate averaging 18% because recall reminders are manual and inconsistent.",
                        rationale="Validated in 12 interviews. Every owner mentioned it unprompted.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What does the practice owner currently do when a patient no-shows that your product would replace?",
                        why_this_matters="Understanding the workaround tells you how much friction you're competing with, and what 'good enough' looks like to them today.",
                        user_answer="Front desk calls the patient, leaves a voicemail, marks it in a paper log, and then remembers (or forgets) to follow up again in two weeks. Three people touched it, none of them consistently.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Solution",
                icon="💡",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Automated recall sequence: text → email → call, spaced at 2 / 7 / 21 days post-visit.",
                        rationale="Most competitors stop at one channel. The third-touch phone call is the differentiator — practices told us voice still converts for older patients.",
                    ),
                    ExampleDecisionSeed(
                        statement="No app required for patients — everything happens over SMS and email.",
                        rationale="Patient adoption is the graveyard of dental software. Removing the install friction is the whole game.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What would make a dentist unsubscribe in month two?",
                        why_this_matters="Churn drivers at month two are almost always onboarding failures or unmet implicit expectations — good to surface now.",
                        user_answer="If patients complain that the texts feel spammy or that the timing is off. Practices are terrified of anything that could damage the patient relationship.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Pricing",
                icon="💰",
                decisions=[
                    ExampleDecisionSeed(
                        statement="$149/month per location, flat rate, billed annually.",
                        rationale="Simple enough for a solo owner to approve without a CFO. Saves them $1,500–$3,000/year in recovered revenue on conservative assumptions.",
                    ),
                    ExampleDecisionSeed(
                        statement="No per-message fees — unlimited SMS/email included.",
                        rationale="Practices hate usage-based billing for anything they can't predict. Flat rate removes the mental accounting.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Go-to-market",
                icon="📣",
                decisions=[
                    ExampleDecisionSeed(
                        statement="First 20 customers via direct outreach to practices within 30 miles of the founding team.",
                        rationale="Proximity lets us do in-person onboarding and watch the product get used. This is the learning channel, not the scale channel.",
                    ),
                    ExampleDecisionSeed(
                        statement="Dental hygienist associations as a referral channel after month 6.",
                        rationale="Hygienists are the ones who feel the no-show pain most acutely — they lose chair time, not revenue. They talk to each other.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What does a dentist need to see or feel before they'll give you access to their patient data?",
                        why_this_matters="Trust is the sales bottleneck in healthcare-adjacent software. Knowing this shapes your onboarding sequence and your security messaging.",
                        user_answer="A live reference call with another practice they trust. Not a case study — an actual conversation where they can ask questions a marketing team wouldn't answer.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Competition",
                icon="🏁",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Weave and Podium are the known competitors, but they serve multi-location practices and are overbuilt for solos.",
                        rationale="Solos told us Weave 'feels like it was built for someone else.' That's our positioning in one sentence.",
                    ),
                    ExampleDecisionSeed(
                        statement="We do not compete on price alone — we compete on fit.",
                        rationale="Anyone can be cheaper. Practices stick with tools that feel made for them.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Milestones",
                icon="🗓️",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Month 3: 10 paying practices, no churn.",
                        rationale="Signal that the product works well enough to retain before we spend on growth.",
                    ),
                    ExampleDecisionSeed(
                        statement="Month 9: $15k MRR, raise a seed round or reach profitability.",
                        rationale="At $149/seat, ~100 practices. Either outcome is a valid next chapter.",
                    ),
                ],
                turns=[],
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # 3. Dinner party event
    # ------------------------------------------------------------------
    ExampleProjectSeed(
        slug="event",
        display_name="A dinner party",
        one_liner="A 40-person seated dinner for a friend's 40th birthday.",
        domain="personal",
        topics=[
            ExampleTopicSeed(
                title="Guest list",
                icon="👥",
                decisions=[
                    ExampleDecisionSeed(
                        statement="40 guests — cap hard at this number, venue won't comfortably fit more.",
                        rationale="Once you say 'maybe 45,' you're at 55. Hold the line.",
                    ),
                    ExampleDecisionSeed(
                        statement="The guest list is managed entirely by the birthday person — no surprises.",
                        rationale="It's their party. We're not making decisions about who's in and who's out without them.",
                    ),
                    ExampleDecisionSeed(
                        statement="Collect dietary restrictions 3 weeks out via a short form.",
                        rationale=None,
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="Are there any guests whose relationship with each other might affect how you arrange seating?",
                        why_this_matters="Seating conflicts discovered at the event are much harder to resolve than ones mapped in advance.",
                        user_answer="Two couples who used to be close but had a falling out last year. Separate tables, not just chairs. They're both coming because they both matter to the birthday person.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Venue",
                icon="🏛️",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Renting a private dining room at a local restaurant rather than using someone's home.",
                        rationale="Hosting 40 people in a house means someone's home is destroyed for a week. The rental is worth it.",
                    ),
                    ExampleDecisionSeed(
                        statement="The restaurant handles setup, breakdown, and food — we bring cake and music.",
                        rationale="Limiting our operational surface area. The more we delegate, the more we can be guests.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What's the venue's policy on outside food and decoration?",
                        why_this_matters="Custom cake and a playlist sound simple until the venue has restrictions you didn't know about.",
                        user_answer="They allow outside cake if it's from a licensed bakery. Decorations are fine as long as nothing goes on the walls. We can run a Spotify playlist through their sound system.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Menu",
                icon="🍽️",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Set menu with three options: fish, meat, vegetarian. Vegan by request.",
                        rationale="More options than three becomes a logistics problem for the kitchen. Three is dignified.",
                    ),
                    ExampleDecisionSeed(
                        statement="The birthday person picks the dessert — it's the one thing they care most about.",
                        rationale=None,
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Program",
                icon="🎙️",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Two short toasts — no more than 3 minutes each. We're not doing a roast.",
                        rationale="Roasts work when the room is all old friends. This is a mixed group. Short and warm beats long and uneven.",
                    ),
                    ExampleDecisionSeed(
                        statement="No formal schedule published — dinner flows naturally, toasts happen after main course.",
                        rationale="Printed programs make it feel like a wedding. This is a dinner party.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="Who gives the toasts, and have they been asked?",
                        why_this_matters="Surprise toasters either give a speech they've been composing for years or panic at the microphone. Neither is great.",
                        user_answer="The birthday person's best friend from college and their sibling. Both have been asked and both said yes. The sibling wants a few prompts to work from.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Logistics",
                icon="🚗",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Invitations sent 6 weeks out, RSVP deadline 3 weeks out.",
                        rationale="6 weeks is enough for people to clear their calendars; 3 weeks gives the restaurant enough lead time.",
                    ),
                    ExampleDecisionSeed(
                        statement="We'll confirm final headcount with the venue 10 days before.",
                        rationale=None,
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Budget",
                icon="💳",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Total budget: $3,200. Venue/food: $2,400. Cake: $300. Flowers: $300. Miscellaneous: $200.",
                        rationale="Per-head cost is $80, which is manageable when split among the people contributing.",
                    ),
                    ExampleDecisionSeed(
                        statement="Costs split among 8 friends contributing equally.",
                        rationale="Agreed in advance in writing — not a fun conversation, but a necessary one.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What's your contingency if the final headcount drops significantly?",
                        why_this_matters="Restaurants often charge per-head minimums, so late dropouts can leave you paying for people who didn't come.",
                        user_answer="The venue has a 35-person minimum for the private room. If we drop below that we lose the room or pay the difference. We've built a buffer by assuming 38 confirmed guests.",
                    ),
                ],
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # 4. Research project
    # ------------------------------------------------------------------
    ExampleProjectSeed(
        slug="research",
        display_name="A research project",
        one_liner="How do small teams (3–8 people) make high-stakes decisions under time pressure?",
        domain="academic",
        topics=[
            ExampleTopicSeed(
                title="Question",
                icon="🔬",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Central research question: do small teams with flat hierarchies make better decisions under pressure than those with a designated leader?",
                        rationale="'Better' is operationalized as: fewer regretted decisions at 30-day follow-up and faster time-to-action.",
                    ),
                    ExampleDecisionSeed(
                        statement="Scope: knowledge-work teams only (product, research, editorial). Not emergency services or military — different dynamics.",
                        rationale="Avoids the well-studied crisis literature, which has its own field.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What would a null result look like, and would that still be worth publishing?",
                        why_this_matters="Research that can only confirm its hypothesis is hard to design well. Knowing what the negative space looks like sharpens the question.",
                        user_answer="If hierarchy and flat structure produce indistinguishable outcomes, the interesting finding would be about what variables actually matter — maybe team tenure or prior shared experience. Yes, that's publishable.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Methodology",
                icon="🧪",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Mixed methods: structured interviews + a decision log survey administered at the end of each team's sprint.",
                        rationale="Interviews capture narrative and context; the survey gives us comparable quantitative data across teams.",
                    ),
                    ExampleDecisionSeed(
                        statement="15–20 teams, minimum 3 months of observation per team.",
                        rationale="Enough for a decision cycle to play out and for 30-day regret follow-up to be meaningful.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="How will you gain access to teams willing to be observed during high-stakes decisions?",
                        why_this_matters="Researcher access is the most common failure point in organizational studies.",
                        user_answer="We're approaching teams through professional networks where we already have trust. Three product teams have agreed informally. We'll also post in two startup Slack communities.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Sources",
                icon="📚",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Grounding in Edmondson's work on psychological safety and Weick's sensemaking theory.",
                        rationale="Both are cited frequently enough in the adjacent literature that reviewers will expect engagement with them.",
                    ),
                    ExampleDecisionSeed(
                        statement="We'll do a systematic literature review covering 2010–present only.",
                        rationale="Pre-2010 research predates the modern distributed-team structure. Including it muddies more than it clarifies.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Timeline",
                icon="📅",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Month 1–2: literature review and instrument design.",
                        rationale=None,
                    ),
                    ExampleDecisionSeed(
                        statement="Month 3–8: data collection.",
                        rationale="Six months gives enough runway to hit 15 teams even if early access requests take longer than expected.",
                    ),
                    ExampleDecisionSeed(
                        statement="Month 9–11: analysis. Month 12: write-up and submission.",
                        rationale=None,
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Output",
                icon="📄",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Primary output: peer-reviewed paper targeting CSCW or Management Science.",
                        rationale="Both have strong practitioner readership — not just academic.",
                    ),
                    ExampleDecisionSeed(
                        statement="Secondary output: a practitioner summary (≤2,000 words) for the teams we studied.",
                        rationale="Reciprocity. The teams gave us access; we owe them something useful in return.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What does success look like if the paper is rejected?",
                        why_this_matters="Knowing your floor helps you decide how much risk to take with the primary venue.",
                        user_answer="Conference presentation, then revise and resubmit somewhere with a faster review cycle. The dataset is worth publishing regardless of where it lands.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Distribution",
                icon="📡",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Preprint on arXiv immediately after submission.",
                        rationale="Makes the work citable during the (potentially long) review window.",
                    ),
                    ExampleDecisionSeed(
                        statement="Share results back to participating teams before public release.",
                        rationale="Ethical obligation and good relationship maintenance for any follow-on research.",
                    ),
                ],
                turns=[],
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # 5. Career transition
    # ------------------------------------------------------------------
    ExampleProjectSeed(
        slug="career",
        display_name="A career transition",
        one_liner="Leaving a senior design role at a tech company to go independent as a consulting designer.",
        domain="career",
        topics=[
            ExampleTopicSeed(
                title="Current situation",
                icon="🏢",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Current role: Principal Designer at a Series C SaaS company. 7 years of in-house experience.",
                        rationale=None,
                    ),
                    ExampleDecisionSeed(
                        statement="The honest reason for leaving: the work has become process management, not design. Last shipped something I'm proud of 18 months ago.",
                        rationale="Important to be honest with myself about why. 'Better work-life balance' is partly true but it's not the whole story.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What would make you turn around and stay?",
                        why_this_matters="Knowing your conditions for staying sharpens whether this is a 'change the context' problem or a 'change the work' problem.",
                        user_answer="A role that's still mostly IC design work with a meaningful scope — not management, not process ownership. Honestly, I've looked for that internally for two years. I don't think this company has it.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Target",
                icon="🎯",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Target clients: Series A–B startups who need a fractional Head of Design for 6–12 months.",
                        rationale="They need the output but can't afford a full-time senior hire. I can do the work of two people for three months and set them up to hire sustainably.",
                    ),
                    ExampleDecisionSeed(
                        statement="Niche: B2B product design. Not branding, not agency-style campaign work.",
                        rationale="This is where my credibility is deepest. Starting in a niche is better than being generic.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What's the smallest first client you'd take to prove the model?",
                        why_this_matters="The first client is a learning environment, not a revenue vehicle. Calibrating the minimum helps you say yes to things you'd otherwise filter out.",
                        user_answer="A founder I know who's building a tool for internal operations teams. Small scope, 2 months, agreed on scope upfront. Would give me something real to point to.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Financials",
                icon="💵",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Target rate: $12,000/month for a 3-day-per-week engagement.",
                        rationale="Based on comparable fractional design rates in my market. Leaves two days for business development and other clients.",
                    ),
                    ExampleDecisionSeed(
                        statement="Personal runway: 8 months of savings. Target: first paying client within 3 months.",
                        rationale="8 months is enough to be discerning about clients. 3 months is a realistic timeline given existing network.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What's your minimum viable monthly income to feel stable, and how many clients does that require?",
                        why_this_matters="The difference between 'I need one client' and 'I need three clients' is a completely different business model.",
                        user_answer="$10k/month covers everything including health insurance. One good client. Two smaller ones also works. I don't want more than two concurrent engagements until I understand the overhead.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Pipeline",
                icon="🔗",
                decisions=[
                    ExampleDecisionSeed(
                        statement="First 90 days: reach out to 30 contacts from past jobs. No cold outreach until the warm network is exhausted.",
                        rationale="Referrals from people who've seen my work close 10x faster than cold outreach. Use the asset I have.",
                    ),
                    ExampleDecisionSeed(
                        statement="No posting on LinkedIn until I have one client and a story to tell.",
                        rationale="'I'm now freelance' content without proof is noise. 'Here's what I learned on a recent engagement' is signal.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Brand",
                icon="🎨",
                decisions=[
                    ExampleDecisionSeed(
                        statement="No agency name — consulting under my own name.",
                        rationale="My reputation is the asset. Hiding it behind a brand name before I have the volume to staff a real team would be premature.",
                    ),
                    ExampleDecisionSeed(
                        statement="Portfolio site focuses on outcomes (what shipped, what changed) not process (how I run workshops).",
                        rationale="Founders want to know what they'll get, not how I'll get there.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Milestones",
                icon="📌",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Month 1: inform manager, negotiate end date, formalize referral relationships with 3 people.",
                        rationale=None,
                    ),
                    ExampleDecisionSeed(
                        statement="Month 3: first paid engagement signed.",
                        rationale="If no signed client by month 3, revisit the rate, niche, or both — not the plan.",
                    ),
                    ExampleDecisionSeed(
                        statement="Month 8: evaluate whether to stay solo or hire a junior designer.",
                        rationale="That's the fork in the road — either I'm content doing the work myself, or I want to build something bigger.",
                    ),
                ],
                turns=[],
            ),
        ],
    ),

    # ------------------------------------------------------------------
    # 6. Paid newsletter
    # ------------------------------------------------------------------
    ExampleProjectSeed(
        slug="side_project",
        display_name="A paid newsletter",
        one_liner="A weekly paid newsletter covering local urbanism — transit, housing, and city-making.",
        domain="media",
        topics=[
            ExampleTopicSeed(
                title="Core idea",
                icon="🏙️",
                decisions=[
                    ExampleDecisionSeed(
                        statement="The newsletter covers the civic decisions that shape a specific city — Seattle — not urbanism in the abstract.",
                        rationale="There are plenty of urbanism newsletters. None of them care about the Route 7 bus restructure or the South Lake Union rezoning fight. That specificity is the point.",
                    ),
                    ExampleDecisionSeed(
                        statement="Each issue: one long-form piece (800–1,200 words) + a short 'what I'm watching' section.",
                        rationale="Long form is the reason people pay. The short section keeps it feeling alive between the big pieces.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What's the claim your newsletter makes about the world that not everyone agrees with?",
                        why_this_matters="A point of view attracts readers who share it and repels those who don't — which is exactly what you want. Generic coverage doesn't build a following.",
                        user_answer="That local governments are systematically bad at communicating the tradeoffs behind their decisions, and that informed residents who understand those tradeoffs make better neighbors and voters. I want to be the translation layer.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Audience",
                icon="👥",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Target reader: a Seattle resident who cares about their city, takes the bus sometimes, and has opinions about the missing middle housing debate — but doesn't follow city council meetings.",
                        rationale="Not urbanists. Not housing activists. People who'd call themselves 'engaged citizens' before they'd call themselves 'policy nerds.'",
                    ),
                    ExampleDecisionSeed(
                        statement="Not targeting elected officials or developers — they have their own information channels.",
                        rationale="Writing for insiders changes the voice and makes the newsletter less useful to the actual audience.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="Where do your potential readers currently get this type of information, and what's wrong with those sources?",
                        why_this_matters="The gap between existing sources and what readers actually need is your differentiation.",
                        user_answer="Local newspaper covers it episodically and without context. Twitter urbanism accounts are too inside-baseball. Most people just hear about a decision after it's been made and don't understand why. That lag and that lack of context is what I'm solving.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Cadence",
                icon="📆",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Weekly, every Thursday morning.",
                        rationale="Consistent day matters more than consistent time. Thursday gives readers something to read over the weekend when they have more bandwidth.",
                    ),
                    ExampleDecisionSeed(
                        statement="One issue per week, no exceptions for 6 months. No 'off weeks' until there's a real archive.",
                        rationale="Reliability is the product. An erratic newsletter is not a newsletter.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Monetization",
                icon="💳",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Paid from issue one — free tier is one issue per month; full access for $8/month or $72/year.",
                        rationale="Starting free and converting later is harder than starting paid and growing slower. I want a real signal on willingness to pay early.",
                    ),
                    ExampleDecisionSeed(
                        statement="No sponsorships until 500 paid subscribers.",
                        rationale="Sponsorships at small scale pay poorly and create editorial awkwardness. Build the audience first.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What's the minimum subscriber count where this becomes worth the time you'll put into it?",
                        why_this_matters="Knowing your break-even helps you decide whether to keep going or change the model when you hit plateaus.",
                        user_answer="200 paid subscribers at $8/month is $1,600/month. That's not a living, but it's meaningful extra income for work I'd do anyway. Below 100 and I'm basically writing for free. That's the floor.",
                    ),
                ],
            ),
            ExampleTopicSeed(
                title="Promotion",
                icon="📢",
                decisions=[
                    ExampleDecisionSeed(
                        statement="First 100 subscribers from my existing Twitter/Bluesky following and friends.",
                        rationale="The people who already trust my perspective are the most likely to subscribe. Use that asset.",
                    ),
                    ExampleDecisionSeed(
                        statement="Guest-post in the local subreddit and two neighbourhood Facebook groups with a free preview issue.",
                        rationale="That's where engaged residents already are. Native content beats self-promotion.",
                    ),
                ],
                turns=[],
            ),
            ExampleTopicSeed(
                title="Success criteria",
                icon="✅",
                decisions=[
                    ExampleDecisionSeed(
                        statement="Month 6 check-in: 150+ paid subscribers, open rate above 45%, at least one issue that sparked a letter from a city official or a follow-up from a local reporter.",
                        rationale="The last one is the signal that the newsletter is actually influencing the conversation, not just commenting on it.",
                    ),
                    ExampleDecisionSeed(
                        statement="If at month 6 I'm below 75 paid subscribers, I'll reassess the topic, not the format.",
                        rationale="The writing is sound. If it's not finding an audience, the subject might be too niche or the framing off. Those are fixable.",
                    ),
                ],
                turns=[
                    ExampleTurnSeed(
                        planner_question="What would you have to believe about yourself and your situation to still be publishing this at year two?",
                        why_this_matters="Identifying your personal sustainability conditions up front helps you design the project so it doesn't burn you out.",
                        user_answer="That the work is making me a better thinker and writer, not just a newsletter operator. If it starts feeling like content production rather than civic journalism I'll know it's drifted.",
                    ),
                ],
            ),
        ],
    ),
]

# Quick lookup by slug
_SEEDS_BY_SLUG: dict[str, ExampleProjectSeed] = {
    s.slug: s for s in EXAMPLE_PROJECTS
}


def instantiate_example_project(
    store: "PlanningStudioStore",
    *,
    user_id: str,
    slug: str,
) -> dict[str, Any]:
    """Create a fully-populated example project under *user_id*.

    Returns the raw project dict (same shape as ``store.create_v2_project``).
    Raises ``ValueError("unknown_example")`` if the slug doesn't exist.
    """
    seed = _SEEDS_BY_SLUG.get(slug)
    if seed is None:
        raise ValueError("unknown_example")

    # ---- Create the project row ----------------------------------------
    project = store.create_v2_project(
        user_id=user_id,
        title=seed.display_name,
    )
    project_id = project["project_id"]

    # Stamp the project metadata so the canvas can render the example banner.
    # We reach into the DB via a raw update rather than adding a kwarg to
    # create_v2_project, keeping the store interface stable.
    import json as _json
    import sqlite3 as _sqlite3

    conn = store._connect()  # noqa: SLF001 — intentional internal access for metadata write
    with conn:
        row = conn.execute(
            "SELECT metadata_json FROM v2_projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        meta = _json.loads(row["metadata_json"] or "{}") if row else {}
        meta["is_example"] = True
        meta["example_slug"] = slug
        conn.execute(
            "UPDATE v2_projects SET metadata_json = ? WHERE project_id = ?",
            (_json.dumps(meta), project_id),
        )
    conn.close()

    # Refresh the local project dict with the updated metadata
    project["metadata"] = {**project.get("metadata", {}), "is_example": True, "example_slug": slug}

    # ---- Layout helper (mirrors the templates route in api.py) ---------
    # Simple 2-row zigzag — same as the template handler. The frontend
    # will run its own auto-layout on canvas open; our positions are a
    # reasonable fallback.
    x_step = 440
    y_rows = [0, 320]

    for idx, topic_seed in enumerate(seed.topics):
        topic = store.create_topic(
            project_id=project_id,
            title=topic_seed.title,
            icon=topic_seed.icon,
            position_x=float((idx // len(y_rows)) * x_step),
            position_y=float(y_rows[idx % len(y_rows)]),
            origin="planner_initial",
            order_index=idx,
            user_id=user_id,
        )
        topic_id = topic["topic_id"]

        # ---- Decisions -------------------------------------------------
        for dec in topic_seed.decisions:
            store.create_decision(
                topic_id=topic_id,
                project_id=project_id,
                statement=dec.statement,
                rationale=dec.rationale,
                proposed_by="planner",
                status="confirmed",
            )

        # ---- Q&A turns -------------------------------------------------
        for turn_seed in topic_seed.turns:
            # Planner turn first
            store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="planner",
                body=turn_seed.planner_question,
                why_this_matters=turn_seed.why_this_matters,
                status="answered",
            )
            # Then the user's answer
            store.append_qna_turn(
                topic_id=topic_id,
                project_id=project_id,
                role="user",
                body=turn_seed.user_answer,
                status="answered",
            )

    return project
