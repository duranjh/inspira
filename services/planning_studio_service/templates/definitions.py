"""The ten starter-pack template definitions.

Written as module-level ``Template`` objects (not JSON) so they're
type-checked alongside the rest of the service and participate in
import-time validation. Editorial rules:

- Warm, editorial tone — short sentences, first-person pitches where
  natural, no enterprise / PM jargon.
- No emojis anywhere (product rule).
- Icons drawn from the same small lexicon the kickoff planner uses
  (map-pin, clock, heart, chart, flag, etc.) so they render through the
  existing canvas icon mapping.
- Each template: 5-7 topics, 5-8 directed relationships with verb labels
  ("shapes", "informs", "drives", "depends on", "precedes", "supports").
- Relationships reference topic titles, not IDs — the HTTP layer maps
  titles to freshly-minted topic IDs when it seeds a new project.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TemplateTopic:
    """A single seeded topic inside a template.

    ``why_this_topic`` is stored in the topic's metadata (same slot the
    planner's kickoff response uses) so the canvas tooltip / topic
    detail can surface the authored rationale alongside planner output.
    """

    title: str
    icon: str
    why_this_topic: str


@dataclass(frozen=True)
class TemplateRelationship:
    """A directed edge between two seeded topics.

    Endpoints are topic titles (canonical within the template). The HTTP
    layer resolves them to the actual topic IDs once the topics are
    persisted, matching the same pattern used by the kickoff endpoint.
    """

    from_title: str
    to_title: str
    label: str


@dataclass(frozen=True)
class Template:
    """A starter pack: metadata + topics + relationships."""

    slug: str
    title: str
    tagline: str
    description: str
    domain_framing: str
    topics: tuple[TemplateTopic, ...]
    relationships: tuple[TemplateRelationship, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# 1. Novel
# ---------------------------------------------------------------------------
_NOVEL = Template(
    slug="novel",
    title="Novel",
    tagline="A first draft you can actually finish.",
    description=(
        "The scaffolding for a literary or commercial novel — characters, "
        "world, and plot mapped well enough that the first draft has a spine."
    ),
    domain_framing="novel",
    topics=(
        TemplateTopic(
            title="Characters",
            icon="heart",
            why_this_topic=(
                "Who's on the page, what they want, and what keeps them "
                "from getting it."
            ),
        ),
        TemplateTopic(
            title="Setting",
            icon="map-pin",
            why_this_topic=(
                "The time and place the story breathes in — specific "
                "enough to be felt, loose enough to move inside."
            ),
        ),
        TemplateTopic(
            title="Plot Structure",
            icon="flag",
            why_this_topic=(
                "Act shape, inciting event, turns, and the climax the "
                "whole thing is walking toward."
            ),
        ),
        TemplateTopic(
            title="Themes",
            icon="book",
            why_this_topic=(
                "What the novel is quietly arguing about — what lingers "
                "after the last page."
            ),
        ),
        TemplateTopic(
            title="Voice",
            icon="mic",
            why_this_topic=(
                "Point of view, tense, tone — the register a reader "
                "hears on line one and trusts by line ten."
            ),
        ),
        TemplateTopic(
            title="Act Breakdown",
            icon="list",
            why_this_topic=(
                "Scene-level map of the three acts so the middle doesn't "
                "collapse under its own weight."
            ),
        ),
        TemplateTopic(
            title="Revision Plan",
            icon="check",
            why_this_topic=(
                "What you'll do after the draft lands — reader rounds, "
                "structural passes, and when to stop."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Characters", "Plot Structure", "drives"),
        TemplateRelationship("Setting", "Themes", "shapes"),
        TemplateRelationship("Themes", "Voice", "informs"),
        TemplateRelationship("Plot Structure", "Act Breakdown", "precedes"),
        TemplateRelationship("Characters", "Voice", "shapes"),
        TemplateRelationship("Voice", "Revision Plan", "supports"),
        TemplateRelationship("Act Breakdown", "Revision Plan", "precedes"),
    ),
)


# ---------------------------------------------------------------------------
# 2. Clothing brand
# ---------------------------------------------------------------------------
_CLOTHING_BRAND = Template(
    slug="clothing-brand",
    title="Clothing brand",
    tagline="Direct-to-consumer basics, one season at a time.",
    description=(
        "A small apparel brand — what it stands for, who it dresses, what "
        "it makes, and how it reaches the first hundred real customers."
    ),
    domain_framing="business",
    topics=(
        TemplateTopic(
            title="Brand & Positioning",
            icon="flag",
            why_this_topic=(
                "Who you are to a customer, what you promise, and what "
                "you quietly refuse to be."
            ),
        ),
        TemplateTopic(
            title="Target Customer",
            icon="heart",
            why_this_topic=(
                "The specific person you're dressing — their wardrobe "
                "gaps, price tolerance, and how they find new brands."
            ),
        ),
        TemplateTopic(
            title="Product Line",
            icon="box",
            why_this_topic=(
                "The first three to five pieces — fabric, fit, and how "
                "they hang together as a starter wardrobe."
            ),
        ),
        TemplateTopic(
            title="Sourcing",
            icon="link",
            why_this_topic=(
                "Mills, factories, and MOQs — the supply chain that "
                "makes the first season actually produceable."
            ),
        ),
        TemplateTopic(
            title="Channels",
            icon="bolt",
            why_this_topic=(
                "Where you sell — your own site, wholesale, pop-ups — "
                "and which one leads the first twelve months."
            ),
        ),
        TemplateTopic(
            title="Pricing",
            icon="chart",
            why_this_topic=(
                "Landed cost, markup, and whether the price a customer "
                "sees tells a coherent story about the brand."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Brand & Positioning", "Channels", "shapes"),
        TemplateRelationship("Target Customer", "Product Line", "drives"),
        TemplateRelationship("Product Line", "Sourcing", "depends on"),
        TemplateRelationship("Sourcing", "Pricing", "informs"),
        TemplateRelationship("Brand & Positioning", "Pricing", "shapes"),
        TemplateRelationship("Target Customer", "Channels", "informs"),
    ),
)


# ---------------------------------------------------------------------------
# 3. Marketing launch
# ---------------------------------------------------------------------------
_MARKETING_LAUNCH = Template(
    slug="marketing-launch",
    title="Marketing launch",
    tagline="A launch you can run twice.",
    description=(
        "A scoped product or feature launch — audience, story, channels, "
        "and the week of that actually turns into something repeatable."
    ),
    domain_framing="launch",
    topics=(
        TemplateTopic(
            title="Target Audience",
            icon="heart",
            why_this_topic=(
                "The narrow slice of people this launch is for — where "
                "they already spend their time."
            ),
        ),
        TemplateTopic(
            title="Messaging",
            icon="mic",
            why_this_topic=(
                "The three sentences a stranger has to understand in "
                "under thirty seconds."
            ),
        ),
        TemplateTopic(
            title="Channels",
            icon="bolt",
            why_this_topic=(
                "Paid, owned, earned — which levers you're pulling and "
                "which you're deliberately not."
            ),
        ),
        TemplateTopic(
            title="Content Calendar",
            icon="calendar",
            why_this_topic=(
                "What ships when — four weeks of posts, emails, and "
                "assets that land in sequence."
            ),
        ),
        TemplateTopic(
            title="Creative Assets",
            icon="box",
            why_this_topic=(
                "The art, copy, and video the calendar needs — and who "
                "makes each piece in what order."
            ),
        ),
        TemplateTopic(
            title="Launch Week",
            icon="flag",
            why_this_topic=(
                "The seven days around go-live — pre-briefs, day-of "
                "cadence, and the win condition."
            ),
        ),
        TemplateTopic(
            title="Post-launch",
            icon="check",
            why_this_topic=(
                "Retention motion, learnings, and what the second run "
                "of this launch would change."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Target Audience", "Messaging", "shapes"),
        TemplateRelationship("Messaging", "Content Calendar", "drives"),
        TemplateRelationship("Channels", "Content Calendar", "shapes"),
        TemplateRelationship("Content Calendar", "Creative Assets", "requires"),
        TemplateRelationship("Creative Assets", "Launch Week", "precedes"),
        TemplateRelationship("Launch Week", "Post-launch", "precedes"),
        TemplateRelationship("Target Audience", "Channels", "informs"),
    ),
)


# ---------------------------------------------------------------------------
# 4. Research project
# ---------------------------------------------------------------------------
_RESEARCH_PROJECT = Template(
    slug="research-project",
    title="Research project",
    tagline="From questions to a report you'll be proud of.",
    description=(
        "A scoped qualitative or mixed-methods study — from a sharp "
        "research question to a deliverable someone will actually read."
    ),
    domain_framing="research",
    topics=(
        TemplateTopic(
            title="Research Question",
            icon="flag",
            why_this_topic=(
                "The single question the study is in service of — "
                "tight enough to answer, broad enough to matter."
            ),
        ),
        TemplateTopic(
            title="Methodology",
            icon="book",
            why_this_topic=(
                "Interviews, surveys, observations — which methods "
                "earn you the evidence you need."
            ),
        ),
        TemplateTopic(
            title="Participants",
            icon="heart",
            why_this_topic=(
                "Who you're talking to, how you recruit them, and what "
                "screens them in or out."
            ),
        ),
        TemplateTopic(
            title="Interview Guide",
            icon="mic",
            why_this_topic=(
                "The actual questions you'll ask — the shape of the "
                "conversation, not a script."
            ),
        ),
        TemplateTopic(
            title="Analysis Plan",
            icon="chart",
            why_this_topic=(
                "How you move from transcripts to patterns — coding, "
                "synthesis rituals, and who else looks at them."
            ),
        ),
        TemplateTopic(
            title="Deliverable Format",
            icon="box",
            why_this_topic=(
                "Report, deck, memo, or workshop — the shape of what "
                "lands on stakeholders' desks at the end."
            ),
        ),
        TemplateTopic(
            title="Timeline",
            icon="clock",
            why_this_topic=(
                "The calendar that ties recruiting, interviewing, and "
                "analysis into something that finishes."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Research Question", "Methodology", "drives"),
        TemplateRelationship("Methodology", "Participants", "shapes"),
        TemplateRelationship("Methodology", "Interview Guide", "shapes"),
        TemplateRelationship("Participants", "Interview Guide", "informs"),
        TemplateRelationship("Interview Guide", "Analysis Plan", "precedes"),
        TemplateRelationship("Analysis Plan", "Deliverable Format", "informs"),
        TemplateRelationship("Timeline", "Deliverable Format", "supports"),
    ),
)


# ---------------------------------------------------------------------------
# 5. Career move
# ---------------------------------------------------------------------------
_CAREER_MOVE = Template(
    slug="career-move",
    title="Career move",
    tagline="Figure out what you want before you quit.",
    description=(
        "A structured think-through of a significant career change — "
        "before you tell a boss, a partner, or a LinkedIn post."
    ),
    domain_framing="career",
    topics=(
        TemplateTopic(
            title="Current Situation",
            icon="map-pin",
            why_this_topic=(
                "Where you are honestly — comp, workload, energy, and "
                "what still works about it."
            ),
        ),
        TemplateTopic(
            title="What I Want",
            icon="heart",
            why_this_topic=(
                "The life on the other side of the move — named "
                "specifically, not just in adjectives."
            ),
        ),
        TemplateTopic(
            title="Trade-offs",
            icon="chart",
            why_this_topic=(
                "What you're giving up to get it — the things that "
                "are easy to minimise until you're living them."
            ),
        ),
        TemplateTopic(
            title="Financial Runway",
            icon="box",
            why_this_topic=(
                "How long the numbers buy you — and what the tightest "
                "credible version looks like."
            ),
        ),
        TemplateTopic(
            title="Positioning",
            icon="flag",
            why_this_topic=(
                "How you'll introduce yourself after the move — the "
                "phrase that goes in the bio."
            ),
        ),
        TemplateTopic(
            title="First 90 Days",
            icon="calendar",
            why_this_topic=(
                "The three months you'll actually build into something "
                "— people, wins, and a pace you can hold."
            ),
        ),
        TemplateTopic(
            title="Risk Plan",
            icon="bolt",
            why_this_topic=(
                "What makes this go sideways, and what the graceful "
                "fallback actually looks like."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Current Situation", "What I Want", "informs"),
        TemplateRelationship("What I Want", "Trade-offs", "surfaces"),
        TemplateRelationship("Financial Runway", "Trade-offs", "shapes"),
        TemplateRelationship("What I Want", "Positioning", "drives"),
        TemplateRelationship("Positioning", "First 90 Days", "shapes"),
        TemplateRelationship("First 90 Days", "Risk Plan", "informs"),
        TemplateRelationship("Financial Runway", "Risk Plan", "supports"),
    ),
)


# ---------------------------------------------------------------------------
# 6. Event
# ---------------------------------------------------------------------------
_EVENT = Template(
    slug="event",
    title="Event",
    tagline="An event that feels less like a production.",
    description=(
        "A single-day or multi-day gathering — small enough to feel "
        "personal, organised enough to not fall over on the day."
    ),
    domain_framing="event",
    topics=(
        TemplateTopic(
            title="Audience & Invite List",
            icon="heart",
            why_this_topic=(
                "Who the event is for and who specifically is getting "
                "an invitation — by name, not by persona."
            ),
        ),
        TemplateTopic(
            title="Program",
            icon="list",
            why_this_topic=(
                "The arc of the day — what happens when, and what the "
                "shape of the experience is."
            ),
        ),
        TemplateTopic(
            title="Venue & Logistics",
            icon="map-pin",
            why_this_topic=(
                "Where it's held, how people arrive, and what the space "
                "asks of the program."
            ),
        ),
        TemplateTopic(
            title="Meals",
            icon="box",
            why_this_topic=(
                "What gets eaten and when — often the part guests "
                "remember most clearly."
            ),
        ),
        TemplateTopic(
            title="Budget",
            icon="chart",
            why_this_topic=(
                "The ceiling, the line items, and where you're "
                "willing to go slightly over for texture."
            ),
        ),
        TemplateTopic(
            title="Run of Show",
            icon="clock",
            why_this_topic=(
                "The minute-by-minute script on the day — who does "
                "what and what's in their hand."
            ),
        ),
        TemplateTopic(
            title="Follow-up",
            icon="check",
            why_this_topic=(
                "Notes, photos, thank-yous — the week after that turns "
                "one evening into a relationship."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Audience & Invite List", "Program", "shapes"),
        TemplateRelationship("Program", "Venue & Logistics", "drives"),
        TemplateRelationship("Venue & Logistics", "Meals", "shapes"),
        TemplateRelationship("Program", "Run of Show", "precedes"),
        TemplateRelationship("Budget", "Venue & Logistics", "bounds"),
        TemplateRelationship("Budget", "Meals", "bounds"),
        TemplateRelationship("Run of Show", "Follow-up", "precedes"),
    ),
)


# ---------------------------------------------------------------------------
# 7. Cohort course
# ---------------------------------------------------------------------------
_COURSE = Template(
    slug="course",
    title="Cohort course",
    tagline="A course that students finish.",
    description=(
        "A small cohort-based course — outcomes, syllabus, and rituals "
        "that give students something worth the tuition and the time."
    ),
    domain_framing="education",
    topics=(
        TemplateTopic(
            title="Target Student",
            icon="heart",
            why_this_topic=(
                "Who's in the room — seniority, context, and what "
                "they'd be doing with this hour if not here."
            ),
        ),
        TemplateTopic(
            title="Learning Outcomes",
            icon="flag",
            why_this_topic=(
                "What students can do by the end that they couldn't at "
                "the start — concrete, testable, few."
            ),
        ),
        TemplateTopic(
            title="Syllabus",
            icon="book",
            why_this_topic=(
                "Week-by-week shape — topics, readings, and the arc "
                "that lands all the outcomes."
            ),
        ),
        TemplateTopic(
            title="Assignments",
            icon="list",
            why_this_topic=(
                "The artifacts students actually produce — where "
                "learning becomes their own."
            ),
        ),
        TemplateTopic(
            title="Live Sessions",
            icon="mic",
            why_this_topic=(
                "What happens synchronously — lecture, workshop, "
                "office hours — and what the energy of each is."
            ),
        ),
        TemplateTopic(
            title="Assessment",
            icon="check",
            why_this_topic=(
                "How you'll know it worked — for the student, for you, "
                "and for future cohorts."
            ),
        ),
        TemplateTopic(
            title="Community Platform",
            icon="link",
            why_this_topic=(
                "Where students talk to each other between sessions "
                "and whether that space stays alive past graduation."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Target Student", "Learning Outcomes", "shapes"),
        TemplateRelationship("Learning Outcomes", "Syllabus", "drives"),
        TemplateRelationship("Syllabus", "Assignments", "shapes"),
        TemplateRelationship("Syllabus", "Live Sessions", "shapes"),
        TemplateRelationship("Assignments", "Assessment", "supports"),
        TemplateRelationship("Live Sessions", "Community Platform", "supports"),
        TemplateRelationship("Assessment", "Learning Outcomes", "confirms"),
    ),
)


# ---------------------------------------------------------------------------
# 8. Side project
# ---------------------------------------------------------------------------
_SIDE_PROJECT = Template(
    slug="side-project",
    title="Side project",
    tagline="Small enough to ship; meaningful enough to finish.",
    description=(
        "A personal build — the small thing you're making on the side "
        "that actually wants to get to a version one."
    ),
    domain_framing="software",
    topics=(
        TemplateTopic(
            title="The Core Idea",
            icon="flag",
            why_this_topic=(
                "The one-sentence pitch that survives a month of "
                "doubt — what it is and what it isn't."
            ),
        ),
        TemplateTopic(
            title="Target User",
            icon="heart",
            why_this_topic=(
                "The one specific person you're building for — "
                "sometimes that's you, said honestly."
            ),
        ),
        TemplateTopic(
            title="Minimum Scope",
            icon="box",
            why_this_topic=(
                "The smallest thing that's useful — what has to be "
                "there on day one, and what definitely doesn't."
            ),
        ),
        TemplateTopic(
            title="Tech Stack",
            icon="bolt",
            why_this_topic=(
                "What you're building it with and why — boring picks "
                "for the parts you don't want to argue about."
            ),
        ),
        TemplateTopic(
            title="Weekly Cadence",
            icon="calendar",
            why_this_topic=(
                "How much time you actually have and when — the "
                "pace that survives a real calendar."
            ),
        ),
        TemplateTopic(
            title="Launch Plan",
            icon="check",
            why_this_topic=(
                "How version one reaches the ten people it's for — "
                "post, email, or quiet link to friends."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("The Core Idea", "Target User", "shapes"),
        TemplateRelationship("Target User", "Minimum Scope", "drives"),
        TemplateRelationship("Minimum Scope", "Tech Stack", "informs"),
        TemplateRelationship("Weekly Cadence", "Minimum Scope", "bounds"),
        TemplateRelationship("Minimum Scope", "Launch Plan", "precedes"),
        TemplateRelationship("Target User", "Launch Plan", "shapes"),
    ),
)


# ---------------------------------------------------------------------------
# 9. Podcast
# ---------------------------------------------------------------------------
_PODCAST = Template(
    slug="podcast",
    title="Podcast",
    tagline="A sustainable interview show.",
    description=(
        "An interview podcast — the format, the guests, and the "
        "production rhythm that keeps it alive past the first ten episodes."
    ),
    domain_framing="content",
    topics=(
        TemplateTopic(
            title="Format & Structure",
            icon="list",
            why_this_topic=(
                "Episode length, segments, and the show's signature "
                "shape — what a listener can count on every time."
            ),
        ),
        TemplateTopic(
            title="Guest List",
            icon="heart",
            why_this_topic=(
                "The first ten people you'd love to have on — and the "
                "second ten who you could actually land."
            ),
        ),
        TemplateTopic(
            title="Recording Setup",
            icon="mic",
            why_this_topic=(
                "Mics, room, remote workflow — the technical floor "
                "that every episode has to clear."
            ),
        ),
        TemplateTopic(
            title="Production Cadence",
            icon="clock",
            why_this_topic=(
                "Record, edit, publish — the weekly or biweekly "
                "rhythm that a hobby podcast burns out without."
            ),
        ),
        TemplateTopic(
            title="Distribution Channels",
            icon="bolt",
            why_this_topic=(
                "Where the episodes live — the podcast apps, the "
                "video mirror, the show page."
            ),
        ),
        TemplateTopic(
            title="Audience Building",
            icon="chart",
            why_this_topic=(
                "How the show reaches ears that aren't already your "
                "friends — and what you do with them once it does."
            ),
        ),
        TemplateTopic(
            title="Monetization (Later)",
            icon="box",
            why_this_topic=(
                "What a credible money story looks like — sponsors, "
                "membership, or job search — deferred, not absent."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Format & Structure", "Recording Setup", "shapes"),
        TemplateRelationship("Guest List", "Format & Structure", "informs"),
        TemplateRelationship("Recording Setup", "Production Cadence", "supports"),
        TemplateRelationship("Production Cadence", "Distribution Channels", "precedes"),
        TemplateRelationship("Distribution Channels", "Audience Building", "supports"),
        TemplateRelationship("Audience Building", "Monetization (Later)", "precedes"),
    ),
)


# ---------------------------------------------------------------------------
# 10. Business plan
# ---------------------------------------------------------------------------
_BUSINESS_PLAN = Template(
    slug="business-plan",
    title="Business plan",
    tagline="A business plan that isn't just for the bank.",
    description=(
        "The working plan for a new business — useful for a loan "
        "officer and useful for you on a Tuesday morning in month three."
    ),
    domain_framing="business",
    topics=(
        TemplateTopic(
            title="Vision",
            icon="flag",
            why_this_topic=(
                "Where this is going in five years if it works — said "
                "plainly, without mission-statement fog."
            ),
        ),
        TemplateTopic(
            title="Customer",
            icon="heart",
            why_this_topic=(
                "Who this is for — the person who pays, not the "
                "persona on the pitch deck."
            ),
        ),
        TemplateTopic(
            title="Value Proposition",
            icon="book",
            why_this_topic=(
                "The swap you're offering — what the customer gets, "
                "and why it beats what they do today."
            ),
        ),
        TemplateTopic(
            title="Revenue Model",
            icon="chart",
            why_this_topic=(
                "How money actually flows in — what gets charged, "
                "when, and what the margin looks like."
            ),
        ),
        TemplateTopic(
            title="Go-to-Market",
            icon="bolt",
            why_this_topic=(
                "How the first hundred customers hear about you — and "
                "what the first thousand will cost."
            ),
        ),
        TemplateTopic(
            title="Team & Roles",
            icon="link",
            why_this_topic=(
                "Who's doing what, where the gaps are, and what the "
                "next hire changes about the math."
            ),
        ),
        TemplateTopic(
            title="Financials",
            icon="box",
            why_this_topic=(
                "The numbers that matter — runway, unit economics, "
                "and the honest break-even."
            ),
        ),
        TemplateTopic(
            title="Milestones",
            icon="check",
            why_this_topic=(
                "The next six to twelve months in specific, dated "
                "checkpoints — the things you'll actually ship."
            ),
        ),
    ),
    relationships=(
        TemplateRelationship("Vision", "Value Proposition", "shapes"),
        TemplateRelationship("Customer", "Value Proposition", "drives"),
        TemplateRelationship("Value Proposition", "Revenue Model", "informs"),
        TemplateRelationship("Customer", "Go-to-Market", "drives"),
        TemplateRelationship("Revenue Model", "Financials", "informs"),
        TemplateRelationship("Team & Roles", "Financials", "informs"),
        TemplateRelationship("Go-to-Market", "Milestones", "shapes"),
        TemplateRelationship("Financials", "Milestones", "bounds"),
    ),
)


# Ordered — the frontend renders them in this sequence, which is
# loosely "creative writing → brand / launch → research → life / career
# → event / course → smaller side things → the business plan". Don't
# reorder casually; onboarding copy referenced positions.
TEMPLATES: tuple[Template, ...] = (
    _NOVEL,
    _CLOTHING_BRAND,
    _MARKETING_LAUNCH,
    _RESEARCH_PROJECT,
    _CAREER_MOVE,
    _EVENT,
    _COURSE,
    _SIDE_PROJECT,
    _PODCAST,
    _BUSINESS_PLAN,
)


# Hidden from the kickoff picker AND blocked from POST /from-template.
#
# Two waves:
#
# 1) Doc-type orphans: marketing-launch, course, side-project, podcast,
#    career-move. The LLM-inferred domain doesn't reliably map to a
#    doc_type via store.DOMAIN_TO_DOC_TYPE, OR partner-click testing
#    surfaced an empty-state. Each needs a domain mapping or a
#    dedicated doc-type generator before it can ship.
#
# 2) Sunday-ship YC v4 reframe (2026-05-01): the remaining five
#    consumer-leaning templates (novel, clothing-brand, research-project,
#    event, business-plan) are also hidden. The v4 product framing is
#    "AI thinks from your data" — canned templates of any kind contradict
#    that. The kickoff form's text-area entry point ("Tell me about your
#    idea. A paragraph is plenty") stands alone. Re-enable selectively
#    when shipping software-dev-team-relevant templates post-batch.
#
# Source TEMPLATES tuple stays unchanged so re-enabling is a single-set
# edit, not a content re-author.
DOC_TYPE_ORPHAN_SLUGS: frozenset[str] = frozenset({
    "marketing-launch",
    "course",
    "side-project",
    "podcast",
    "career-move",
    "novel",
    "clothing-brand",
    "research-project",
    "event",
    "business-plan",
})


__all__ = [
    "TEMPLATES",
    "DOC_TYPE_ORPHAN_SLUGS",
    "Template",
    "TemplateRelationship",
    "TemplateTopic",
]
