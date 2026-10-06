# The Financial Rationale Behind Public Pension Reform: General Principles Applied to the Ostonian 2026 Reform

## 1. Scope and method

This report explains the economic and actuarial rationale behind the kind of pension restructuring described in the attached Ostonian documents — the "National Pension System Reform" Green Paper 2026 (`tex.pdf`, Ministry of Social Security, Republic of Ostonia) and the accompanying online benefit estimator (`7.html`). It covers (a) the general principles for this kind of restructuring, (b) how such changes are intended to secure long-term financial health, and (c) the typical economic consequences documented in the research collection at `/app/input/corpus` (155 documents; citations give document ID, title and source URL). It does not attempt an independent actuarial assessment of Ostonia's plan, because the Green Paper is a policy framework that omits most of the underlying projections (see §7, Limitations).

### 1.1 What the Ostonian reform actually does

The Green Paper contains three core measures (all confirmed in `tex.pdf` and the calculator's JavaScript):

- **Phased retirement-age increase**: statutory retirement age rises from 60 to 65 for "Class A" employees over ten years, by birth-year cohort (1966 → 60.5; 1976+ → 65).
- **New benefit formula**: the benefit shifts from a *final-salary* model to a *lifetime-average* model, `P = (W_avg × Y_service) / T_divisor`, where `W_avg` is the inflation-adjusted average monthly wage over the last 20 years, `Y_service` is accredited contribution years, and `T_divisor` is fixed at 140 for 2026. A deferral bonus `α = 0.04` per year applies for retirement deferred beyond the statutory age.
- **Transition rule**: citizens with 35+ contribution years as of 1 January 2026 are exempt from the age delay but *not* from the new formula.

This package — retirement-age increase plus replacement of a final-salary defined-benefit basis by an average-earnings/actuarial-divisor basis — is a textbook example of *parametric* reform of a public pension system, and the research literature explains each element clearly.

## 2. The underlying problem: demographic and fiscal arithmetic

Almost every document in the corpus traces pension reform back to the same driver: population aging straining pay-as-you-go (PAYG) finances.

- Globally, the population aged 60+ is projected to grow from ~962 million (13%) to 1.4 billion by 2030 and 2.1 billion by 2050 (D0022, "Aging Population and its Impacts on Fiscal Sustainability," global-solutions-initiative.org).
- Across the OECD, the share of population aged 65+ more than doubled between 1960 and 2022 to ~18% and is projected to reach ~30% by 2060 (D0029, OECD Ecoscope).
- Aging raises pension and health spending while shrinking the contributor base; absent reform, "large increases in tax revenue would be needed to stabilise public debt" (D0107, OECD Economics Department).
- A European Commission analysis notes that a primary cause of unsustainability is "the failure to adapt to very long-term demographic trends" — rising life expectancy and falling fertility — leaving unfunded systems facing either partial default on promised benefits or very high future taxes (D0113, *European Economic Review*).

When a system's promises outrun its projected revenue, the adjustment can only come through three channels. The French i-MIP study frames it precisely: the funding need created by rising longevity can be closed by (i) higher contribution rates, (ii) lower replacement rates, or (iii) a higher retirement age — or any combination (D0004, i-mip.eu). For France, closing the gap with contributions alone would require raising the rate from 30% to 39% by 2085; with benefit cuts alone, the replacement rate would fall from 60% to 46.2%; with retirement age alone, it would rise from 63 to 66.3. Ostonia's package uses channels (ii) and (iii) simultaneously, leaving contribution rates untouched in the published document.

## 3. The rationale for each design element

### 3.1 Raising the retirement age

This is the most common single lever, and Ostonia's design mirrors international practice:

- **Phased, cohort-based implementation.** China raised its statutory retirement age in 2025 for the first time in 70 years — men 60→63 over 15 years, with gradual six-month steps — explicitly citing life expectancy, longer schooling, deepening aging and a declining working-age population (D0134, Xinhua/SCIO). The US raised its full retirement age from 65 to 67 under the 1983 amendments, phased over 23 years (D0140, Center for Retirement Research; D0152, CBPP). The UK's Pensions Act 2014 institutionalised periodic reviews of State Pension age tied to life-expectancy evidence (D0144, gov.uk). Ostonia's 10-year, cohort-indexed schedule is faster than China's and the US's but structurally identical.
- **Why it works fiscally.** Raising the eligibility age compresses the benefit-payment period and, to the extent people actually work longer, extends the contribution period — improving both sides of the ledger at once. In Austria, raising the early retirement age (ERA) from 60 to 62 for men and 55 to 58.25 for women increased employment among affected workers by ~10–11 percentage points and cut net government expenditure by €107 million (men) and €122 million (women) per birth-year cohort per year of ERA increase, even after accounting for spillovers into unemployment insurance (D0005, Staubli & Zweimüller, PMC). A US Urban Institute estimate found that if all workers delayed retirement just one year in response to a higher full retirement age, the additional payroll and income tax revenue could cover ~28% of the projected annual Social Security deficit 40 years out (D0082, pgpf.org).
- **It is also a benefit cut.** The same mechanism that saves money reduces lifetime transfers: each one-year increase in the US full retirement age is equivalent to roughly a 7% cut in monthly benefits at any claiming age, and raising it to 70 would cut scheduled lifetime benefits for new retirees by ~20% (D0152, CBPP). This is intentional: the retirement-age lever is how many systems implement replacement-rate reductions without saying so explicitly.
- **The deferral bonus.** Ostonia's `α = 0.04` per-year deferral credit is an actuarial-adjustment device. In the US system, benefits claimed early are actuarially reduced and deferred benefits increased (70% of full benefit at 62 vs. 124% at 70), designed to be roughly actuarially neutral over average life expectancy (D0152). A 4% annual deferral credit is modest by international standards and serves mainly as a behavioural nudge toward longer labour-force participation rather than full actuarial compensation.

### 3.2 From final salary to lifetime-average earnings

The second element — replacing the "final salary" basis with a 20-year inflation-adjusted average divided by an actuarial divisor — changes both the level and the distribution of benefits:

- **It removes the late-career spike subsidy.** Final-salary formulas let a worker's entire pension ride on their highest-earning years, which are disproportionately enjoyed by higher-paid, more-educated workers whose wages rise steeply late in career. Because the distribution of labour income becomes more unequal in later working life, formulas that only count part of the contribution history "produce higher replacement rates for more educated workers" and thereby amplify future pension expenditure growth (D0113). Averaging over 20 years tightens the link between lifetime contributions and benefits — moving the plan toward an actuarially "contributory" (Bismarckian) logic where the pension is a return on what was paid in, not a function of terminal rank or a late promotion.
- **It lowers the implicit replacement rate.** The divisor `T = 140` is effectively an annuity conversion factor: a worker with 30 service years earns `30/140 ≈ 21.4%` of their 20-year average wage per month; 40 years yields ~28.6%. Under the old final-salary design (parameters unstated in the Green Paper), benefits typically targeted a higher replacement of *final* — usually peak — salary; the corpus shows the conventional structure — e.g., Minnesota's patrol plan pays a per-service-year multiplier (3%) against a *high-five* average salary, so 25 years of service yields 75% of that peak base (D0008, mnretire.gov). Whether Ostonian retirees are better or worse off depends on each worker's wage trajectory — flat-earning workers may lose little; steep-career workers lose the late-career uplift. The corpus literature (D0113) confirms that this redistribution is a standard, deliberate feature of such reforms.
- **Inflation-adjusted averaging vs. nominal final salary** also protects the fund from the cost of pre-retirement wage surges while preserving real value of earlier contributions — a standard indexation choice (cf. "linking benefits to inflation" discussed as a cost-cutting indexation change in D0129, NBER digest).

### 3.3 The actuarial divisor and life expectancy

Fixing `T_divisor = 140` (months, i.e., ~11.7 years) embeds an assumption about how long benefits will be paid. Two points from the literature matter:

- **Automatic adjustment mechanisms (AAMs)** — sustainability factors that adjust initial benefits for life-expectancy gains, and indexation rules that keep the scheme balanced — are identified as "especially useful in restoring pension sustainability" (D0113). Ostonia's fixed divisor is a *static* version: it only stays solvent if periodically re-set as longevity rises. The UK approach of mandatory periodic state-pension-age reviews (D0144) illustrates the institutionalised alternative.
- **Actuarial valuations are the monitoring tool.** Pension systems rely on periodic actuarial valuations to test whether assumptions (longevity, wage growth, returns) still support promised benefits (D0002, mnpera.org; D0065). The Luxembourg sustainability review shows how sensitive conclusions are to assumptions — its immigration, commuting and 1.2% productivity-growth assumptions were flagged as weakly substantiated (D0038, WIFO). Ostonia's Green Paper publishes no such valuation, so the adequacy of the 140 divisor cannot be verified from the supplied documents.

### 3.4 Transition provisions

Exempting workers with 35+ contribution years from the age delay — while still subjecting them to the new formula — follows a standard political-economy pattern: protect cohorts nearest retirement who cannot adjust behaviour, while still capturing savings from the benefit formula. The literature shows why: reforms that impose short-term welfare losses on near-retirees generate "sizeable political costs" and are frequently reversed — documented reversals include Poland (2016), Germany (2018), Croatia (2019), Netherlands (2019) and Spain (2020–21) (D0113). Gradual phase-ins and grandfathering are the standard mitigation (also argued in D0082). The same source suggests that targeted compensating transfers to harmed groups can make reforms "Pareto-improving" and less vulnerable to reversal; Ostonia's 35-year exemption is a crude version of that logic.

## 4. How these changes are supposed to ensure long-term financial health

The mechanics, synthesised from the corpus:

1. **Fewer beneficiary-years.** Delaying eligibility by up to 5 years removes up to five years of payments per retiree. Compounded over cohorts, this is the largest single saving in the package (D0152 quantifies the equivalence to benefit cuts).
2. **More contributor-years.** Where the age increase translates into actual employment (as in Austria, ~10–11 pp employment gains, D0005), each extra working year adds contributions and income/payroll tax revenue (D0082).
3. **Lower average benefit accrual.** The lifetime-average formula plus a fixed divisor reduces the implicit replacement rate relative to final-salary promises — analogous to the "reduced replacement rate" channel in the i-MIP trilemma (D0004).
4. **Intergenerational sharing of the burden.** Choosing benefit cuts/age increases rather than contribution hikes shifts part of the cost from future workers to current and near-future retirees. The NBER analysis shows the distributional logic explicitly: a pure payroll-tax hike concentrates the burden on the youngest cohorts (lifetime net tax rate for the 1995–2000 cohort rises from 5.4% to 8.4% under a 38% OASI tax hike), whereas benefit cuts spread the burden more evenly across generations (D0129).
5. **Incentive alignment.** Tightening the contribution–benefit link (service-years multiplier, deferral credits) rewards longer contribution histories, raising labour supply at older ages — the objective the Ostonian paper states as "extend the active workforce duration" and consistent with international practice (D0005, D0134).

## 5. Typical economic consequences documented in the literature

### Positive effects (as modelled/estimated)

- **Higher GDP and employment.** Adjusting through the retirement age rather than taxes or benefit cuts produced the best growth outcome in the French model: ~+0.2 percentage points of GDP growth per year, driven by higher employment and higher saving that finances investment (D0004). Delayed retirement increases output, post-retirement living standards and payroll tax revenue (D0082).
- **Improved fiscal balance.** Austrian ERA increases cut net government expenditure by €107–122 million per cohort-year (D0005). More broadly, without such reforms, aging-driven spending cannot be covered by the modest automatic revenue gains from taxing pension income — only ~a quarter of the pension spending increase is recovered that way (D0107).
- **Reduced old-age poverty risk relative to insolvency.** The counterfactual to reform is not stable benefits but eventual inability to pay: unfunded systems face "partial default in promised pension payments" (D0113); US projections anticipate automatic across-the-board cuts (~23%) if trust funds deplete (D0152, D0076).

### Costs and risks

- **Benefit cuts concentrated on those who cannot work longer.** Raising the retirement age is "a blunt instrument that affects both those who can work longer and those who cannot" (D0140). Life-expectancy gains are uneven across the income distribution — the bottom half of US earners saw almost no longevity gains — so uniform age increases cut lifetime benefits most harshly for low-income, shorter-lived workers (D0152, D0140). The Austrian evidence shows the mechanism: low-wage and less-healthy workers bridged the gap through unemployment and disability benefits rather than employment — unemployment rose ~12 pp among affected men (D0005), so part of the pension saving reappears as UI/DI spending and the effective (not statutory) retirement age may barely move for vulnerable groups.
- **Rising inequality.** In the French model, retirement-age adjustment "significantly increases wealth inequality" (Gini) both overall and within age groups, because longer-lived, higher-saving households accumulate more (D0004).
- **Old-age poverty risk for early claimants.** Critics warn that higher statutory ages raise old-age poverty among those who cannot delay claiming (D0082).
- **Political reversal risk.** Reforms imposing concentrated short-term losses are frequently reversed (Poland, Germany, Croatia, Netherlands, Spain; D0113) — which is why transition protection like Ostonia's 35-year rule exists.
- **Macro trade-offs of the chosen channel.** Had a country instead chosen contribution-rate increases, the French model shows that path is the *least* favourable for growth (rising rates eventually shrink labour supply); benefit-only cuts shift the savings burden onto current generations and depress GDP after 2050 (D0004). Ostonia's avoidance of the tax channel is consistent with the growth-maximising choice in that model, but also concentrates costs on retirees and near-retirees.
- **Fiscal drag that reform does not eliminate.** Even with pension reform, aging raises health and long-term-care costs; pension spending is less than 40% of the total aging-driven spending increase in the OECD analysis (D0107), and slower workforce growth mechanically slows aggregate GDP growth roughly one-for-one (D0119, IMF *Finance & Development*). Pension reform manages the pension bill; it does not solve the broader fiscal cost of aging.

## 6. Assessment of Ostonia's package against the literature

- **Consistent with standard practice**: cohort-phased age increase (China, US, Austria, UK precedents), lifetime-average formula replacing final salary (distributionally progressive per D0113), deferral credits, grandfathering for near-retirees.
- **Potentially aggressive pace**: 60→65 in ten years is faster than China (15 yrs for +3), the US (23 yrs for +2), or Austria (16 yrs for +2/+3.25). Faster transitions save more money sooner but heighten the adjustment and political-reversal risks documented in D0113 and D0005 (workers unable to extend careers will pile into UI/DI or face benefit gaps).
- **A hidden distributional choice**: applying the new (typically less generous) formula even to the exempted 35-year cohort means the "exemption" protects timing, not benefit level — the group closest to retirement still absorbs a formula cut, the profile most associated with reversal risk (D0113).
- **A static divisor in a dynamic problem**: fixing `T_divisor = 140` with no announced indexation or review mechanism means longevity gains will quietly erode solvency again; the literature recommends automatic adjustment mechanisms or periodic reviews (D0113, D0144).
- **Missing complements**: the literature emphasises that age increases work fiscally only to the extent older workers can actually remain employed (D0005, D0140); no labour-market, disability-bridge, or compensating-transfer measures accompany the Ostonian package in the supplied documents.

## 7. Limitations and unresolved uncertainties

- **No actuarial baseline.** The Green Paper states the objective ("stabilize the replacement rate") but publishes no dependency-ratio projections, fund balance, contribution rate, old-formula parameters, or depletion date. Claims that the reform "secures" the fund cannot be verified; the corpus itself shows how sensitive sustainability findings are to assumptions (D0038).
- **Magnitude of the benefit cut is unknown.** Without the old final-salary formula's accrual rate, the change in replacement rate for any worker cannot be computed. The new formula's implied replacement (service years ÷ 140) is arithmetic, but the comparison baseline is absent.
- **Country is fictional/abstracted in the corpus.** The corpus contains no Ostonia-specific demographic or macroeconomic data; all consequences above are drawn from international evidence (US, France, Austria, China, OECD aggregates) and may not transfer to Ostonia's labour market, health profile, or informality levels.
- **Calculator discrepancies.** The web calculator computes retirement year as `birthYear + ceil(retirementAge)` (approximation), omits the deferral bonus entirely (stated in its own footnote), and uses a simplified birth-year threshold logic that doesn't capture the 60+6-months half-step precisely (it assigns 60.5 for births ≥1966, which matches, but boundary months are ignored). Its comments are partly in Chinese and it labels itself "System Version 4.2.1" — it should be treated as an estimator, not an authoritative benefit determination.
- **Class A scope undefined.** The Green Paper applies the age delay to "Class A" employees without defining the class; coverage of the reform is therefore unclear.
- **Parliamentary approval pending.** The document explicitly disclaims final implementation details ("subject to parliamentary approval"), matching the corpus finding that pension reforms are frequently modified or reversed in the political process (D0113).

## 8. Bottom line

Ostonia's reform follows the standard parametric-reform playbook used by China, the US, Austria, France and others: push the retirement age up gradually, convert the benefit basis from final salary to lifetime-average contributions converted through an actuarial divisor, add a small deferral incentive, and grandfather those nearest retirement. The financial rationale is that PAYG systems facing rising longevity must adjust contributions, benefits, or retirement duration (D0004); Ostonia chose the two levers the growth literature favours over tax increases — but that choice concentrates losses on future retirees, especially those unable to extend working lives (D0140, D0152, D0005). Whether the package actually achieves long-run solvency cannot be judged from the supplied documents because no actuarial projections are published, and the fixed divisor without an automatic adjustment mechanism is a known weak point relative to international best practice (D0113, D0144).

### Key sources

| ID | Source | Used for |
|----|--------|----------|
| D0004 | i-MIP, Macroeconomic and distributional effects of French pension reforms | The three-channel adjustment trilemma; growth/inequality effects |
| D0005 | Staubli & Zweimüller (PMC), Austrian ERA reforms | Employment effects, UI/DI spillovers, net fiscal savings |
| D0029, D0107 | OECD Ecoscope / OECD Economics Dept | Aging projections; revenue vs. spending gap |
| D0113 | European Economic Review | AAMs, final-salary regressivity, political reversals |
| D0129 | NBER Digest (Gokhale & Kotlikoff) | Intergenerational burden of tax vs. benefit adjustment |
| D0134 | Xinhua/SCIO | China's phased retirement-age reform precedent |
| D0140, D0152 | CRR Boston College; CBPP | Distributional critique; retirement-age = benefit cut |
| D0144 | UK gov.uk | Institutionalised pension-age review mechanism |
| D0082 | Peter G. Peterson Foundation | Arguments for/against raising retirement age; revenue estimates |
| D0022, D0087, D0119 | GSI; CEPR VoxEU; IMF F&D | Macro effects of aging: growth, fiscal pressure |
| D0002, D0038, D0065 | mnpera.org; WIFO; Investopedia | Actuarial valuation practice; assumption sensitivity |
| Attached | tex.pdf (Ostonia Green Paper); 7.html (calculator) | Reform design and formula |
