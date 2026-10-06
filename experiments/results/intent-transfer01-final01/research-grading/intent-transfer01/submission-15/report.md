# AI Business Environment — Research Brief for Funding Pitch

Prepared to support validation of the financial projections in `prediction.csv` and to fill in the market-report outline in `tex.pdf` (a template with placeholder TAM/CAGR figures). All sources are drawn from the supplied corpus (`/app/input/corpus`, referenced by doc_id per `index.json`). No external retrieval was used.

## 1. Snapshot of your projections (context for the market data)

Your model assumes total revenue growing from $1.85M (2024) to $14.9M (2028) — roughly a 68% CAGR — with gross margin held flat near 74%, CAC falling from $250 to $65, LTV rising from $1,000 to $2,900, and headcount growing from 21 to 53 employees. These are aggressive-but-plausible SaaS-style economics; the sections below summarize what the corpus says about whether the market environment supports that growth.

## 2. Overall AI business environment

- **Adoption is broad but shallow.** In McKinsey's latest global survey, 88% of respondents report their organizations regularly use AI in at least one business function (up from 78% a year earlier), yet nearly two-thirds have not begun scaling AI enterprise-wide, and only ~one-third have reached the scaling phase. Only 39% report EBIT-level impact. Larger firms (>$5B revenue) scale at nearly twice the rate of sub-$100M firms (≈50% vs 29%) (D0074).
- **Huge headline opportunity, unclear near-term returns.** McKinsey sizes long-term AI potential at up to $4.4 trillion in added productivity growth; 92% of companies plan to increase AI investment over the next three years, but only 1% of leaders call their companies "mature" in AI deployment (D0191). Separately, McKinsey cites an expected total economic impact of $2.6T–$4.4T annually from gen AI (D0148).
- **Agentic AI is the current frontier.** 62% of surveyed organizations are at least experimenting with AI agents; 23% are scaling an agentic system somewhere, but typically in only one or two functions. Adoption is most common in IT and knowledge management, and in tech, media/telecom, and healthcare industries (D0074). McKinsey's "agentic organization" thesis frames AI agents working alongside humans "at near-zero marginal cost" as the next organizational paradigm (D0019).
- **Massive infrastructure investment is propping up the sector.** OpenAI, Microsoft, Nvidia, Oracle, and SoftBank have struck multi-billion-dollar data-center deals; the Stargate project was seeded with $100B and plans up to $500B; Microsoft alone said it was on track to invest ~$80B in AI-enabled data centers in 2025. WIRED notes this capex wave is "tilting the US into positive GDP territory" (D0167).
- **Adjacent-market tailwinds.** Grand View Research's technology catalog shows the enabling layers growing fast: public cloud $935.7B (2025) → $2.73T (2033) at 14.7% CAGR; customer data platforms at 27.8% CAGR; predictive maintenance at 27.9% CAGR; application security at 18.8% CAGR (D0013, D0093).

## 3. Example startups and competitive signals

- **VoiceRun** — code-first platform for building/deploying AI voice agents; raised a $5.5M seed led by Flybridge Capital (announced Jan 2026). Positions itself between no-code builders (Bland, ReTell AI) and developer-tooling players (LiveKit, Pipecat). Notably, the article states AI-agent startups "nabbed billions of dollars" last year within a broader flood of AI funding — evidence of both capital availability and crowding (D0040).
- **Healthcare AI gold rush** — in a single week: OpenAI acquired health startup Torch; Anthropic launched "Claude for healthcare"; Sam Altman–backed MergeLabs closed a $250M seed at an $850M valuation. Rapid verticalization, but with flagged risks around hallucination, inaccurate medical info, and security of patient data (D0009).
- **Talent wars signal a hot but expensive labor market** — Meta's poaching spree of OpenAI researchers for its superintelligence team (led by Alexandr Wang and Nat Friedman) prompted Altman to review compensation across OpenAI's research org (D0026). For a startup budgeting R&D salaries of $460K–$1.1M/yr (per your CSV), competition for AI talent is a real cost-pressure risk.
- **Skills shortage supports demand for automation/tooling** — 36% of UK vacancies in AI-relevant occupations are attributed to skills shortages; software development shows a 37% deficit (D0058).

## 4. Market health and direction — the bear case

- **Bubble risk is mainstream discourse.** WIRED, applying Goldfarb & Kirsch's bubble framework, notes AI investment is reportedly ~17x pre-dotcom-bust internet investment, with extreme market concentration (Nvidia at times valued near Canada's GDP). Analysts, research firms, and even AI executives concede "some kind" of bubble exists (D0079). A funding pitch should be prepared for bubble questions; the counterargument is that pilots-to-scale conversion, not hype, drives durable revenue.
- **ROI gap is the core risk to your assumptions.** With most buyers still piloting and only ~1 in 3 scaling (D0074), a 68% revenue CAGR requires either landing in the high-performing segment or riding rapid vertical expansion. The pilot-to-production stall is a widely observed failure mode (D0052).
- **Customer caution on trust.** McKinsey's digital-trust survey (1,300+ leaders, 3,000+ consumers) found 40% of consumers have pulled business from a company over data-protection concerns, and trust-leaders are more likely to see ≥10% annual growth — meaning trust/transparency posture is a sales factor, not just compliance (D0098).

## 5. Regulatory and policy items to flag

- **EU AI Act** — imposes risk-based transparency requirements; high-risk systems (e.g., résumé-ranking/recruitment tools) must disclose capabilities, limitations, data lineage, and decision logic. Demand for explainability is rising as a result — relevant both as compliance cost and as product opportunity (D0050). McKinsey describes a fragmented global landscape: broad regulation in the EU and South Korea, sector-specific approaches in the US, and principles-based regimes in Brazil and Singapore, with China also legislating; common themes are transparency, human oversight, accountability, and technical robustness (D0148).
- **Privacy regimes** — GDPR/CCPA consent-management obligations remain an operational baseline for any data-handling AI product (D0007, D0045).
- **Geopolitical/policy events** — the corpus notes a Trump executive order placing TikTok's US operations under Oracle and other US investors, illustrating active government intervention in tech markets (D0186). Stargate also has explicit presidential backing, showing AI infrastructure is now a policy priority (D0167).
- **Multilateral discussions** — UNCTAD16 (Oct 20–23, 2025, Geneva) convenes heads of state and ministers on trade, investment, and the digital economy and sets UNCTAD's four-year work priorities (D0118); the UNCTAD eWeek forum and the UN Commission on Science and Technology for Development continue global digital-governance discussions including WSIS+20 follow-up (D0057, D0170). These shape emerging-market digital-policy direction rather than imposing direct rules.

## 6. Assessment of your assumptions

- **Supportive:** broad and rising adoption (D0074), heavy VC flow into AI-agent startups (D0040, D0009), massive infrastructure commitment (D0167), double-digit CAGRs in enabling markets (D0013). Improving LTV/CAC is directionally consistent with a scaling market.
- **Pressure points:** (a) most buyers don't scale past pilots (D0074) — churn/slow expansion risk; (b) talent costs are being bid up by Big Tech (D0026) — your salary lines look lean relative to that market; (c) flat 74% gross margin is plausible for software but unverified against COGS drivers like inference compute, which the corpus doesn't price; (d) zero taxes through 2028 even at $7.1M net income is optimistic (NOL carryforwards may cover it if 2024–25 losses persist, but that should be documented); (e) bubble risk could compress multiples or customer budgets mid-plan (D0079).

## 7. Limitations and unresolved uncertainties

- `tex.pdf` is a template — its TAM of "$[X] billion" and CAGR "[Y]%" are placeholders; the corpus contains **no single "AI market size" figure**, so your TAM slide needs a number from a source outside this collection (or a bottom-up build from the segment-level GVR figures cited above).
- The corpus is a curated snapshot (mostly late-2025/early-2026 pages); it lacks VC aggregate funding totals, interest-rate/macro data, and your specific target industry (named "[Target Industry]" in the PDF), so vertical-specific validation is not possible from these sources alone.
- D0079 is an opinion/analysis piece, not a market forecast; treat bubble claims as sentiment risk, not fact.
- Regulatory status dates: the McKinsey regulatory piece (D0148) predates full EU AI Act enforcement timelines; confirm current obligations before quoting in a deck.
