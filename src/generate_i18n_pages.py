#!/usr/bin/env python3
"""Generate /en/ localized static pages for true URL-level i18n.

The site is a zero-build static site whose pages carry both languages inline
(``lang-zh-only`` / ``lang-en-only`` blocks + ``data-i18n`` spans) and toggle
language client-side. To give answer engines and search crawlers genuine
per-language URLs (``/en/<page>.html``) instead of only the ``?lang=en`` query
variant, this script mirrors the *static content pages* into ``docs/en/`` with:

  * ``<html lang="en">`` and ``<body class="lang-en">`` so English renders
    immediately (i18n.js also detects the ``/en/`` path and forces English);
  * relative ``css/`` / ``js/`` / ``manifest`` / image paths rewritten to
    ``../`` so they resolve one directory deeper;
  * internal links pointing at pages that also have an ``/en/`` copy kept
    relative, others rewritten to ``../`` (root, where localStorage keeps EN);
  * ``canonical`` / ``og:url`` pointing at the ``/en/`` URL (hreflang on the
    source pages already declares the reciprocal relationship).

Only static pages are mirrored. The interactive dashboard / data pages fetch
JSON relative to their own URL, so an ``/en/`` copy would 404 on data; those
pages intentionally keep the ``?lang=en`` variant instead.

Run from anywhere; idempotent (``docs/en/`` is rebuilt each time).
"""
import json
import re
import pathlib

BASE = "https://s0914712.github.io/taiwan-grayzone-monitor/"
DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"
EN_DIR = DOCS / "en"

# Static content pages safe to mirror — verified to load no JSON-fetching
# modules (only i18n.js + mobile-nav.js). Keep in sync with the app-page set:
# index, dark-vessels, statistics, identity-history, ship-transfers,
# ais-animation, cn-fishing-animation are deliberately NOT mirrored.
STATIC_PAGES = [
    "blog.html",
    "intro.html",
    "research-submarine-cable-legal.html",
    "blog-methodology.html",
    "blog-what-is-ais.html",
    "blog-what-is-submarine-cable.html",
    "blog-cable-threats.html",
    "blog-taiwan-cable-status.html",
    "blog-taiwan-enforcement.html",
    "blog-what-is-dark-vessel.html",
    "blog-what-is-ship-to-ship-transfer.html",
    "blog-what-is-ais-spoofing.html",
    "blog-what-is-maritime-gray-zone.html",
    "blog-what-is-shadow-fleet.html",
    "blog-what-is-maritime-sanctions.html",
    "blog-taiwan-maritime-zones.html",
    "blog-gray-zone-glossary.html",
    "blog-ocean-law-maritime-rights.html",
    "blog-ocean-law-cable-protection.html",
    "blog-ocean-law-enforcement-jurisdiction.html",
    "blog-taiwan-cable-infrastructure.html",
    "blog-global-cable-industry.html",
]
EN_SET = set(STATIC_PAGES)

SITE = "Taiwan Gray Zone Monitor"

# English <head> metadata for each mirrored page. `title` -> <title>;
# `social` -> og:title / twitter:title; `desc` -> description / og:description /
# twitter:description; `kw` -> keywords. A missing source tag is a no-op.
EN_META = {
    "blog.html": {
        "title": f"In-Depth Articles | {SITE}",
        "social": "In-Depth Articles | Taiwan Gray Zone & Submarine Cable Monitor",
        "desc": "In-depth articles on Taiwan submarine-cable security, AIS vessel monitoring, gray-zone tactics, and this project's methodology.",
        "kw": "submarine cable, AIS, gray zone, shadow fleet, Taiwan Strait security, dark vessel",
    },
    "intro.html": {
        "title": f"About | {SITE}",
        "social": "About | Taiwan Gray Zone & Submarine Cable Monitor",
        "desc": "An open-source OSINT project monitoring maritime gray-zone activity and submarine-cable security around Taiwan, with FAQs and a usage guide.",
        "kw": "gray zone, Taiwan Strait, submarine cable, dark vessel, AIS, maritime surveillance, OSINT",
    },
    "research-submarine-cable-legal.html": {
        "title": f"Critical Nodes of an Invisible War: PRC Threats to Taiwan's Submarine Cables | {SITE}",
        "social": "PRC Threats to Taiwan's Submarine Cables",
        "desc": "Research on the legal framework and enforcement challenges around PRC gray-zone threats to Taiwan's submarine cables (UNCLOS, flag-state jurisdiction).",
        "kw": "submarine cable, UNCLOS, flag state, gray zone, Taiwan, legal enforcement",
    },
    "blog-methodology.html": {
        "title": f"Our Methodology — How Vessels Are Scored | {SITE}",
        "social": "Our Methodology — How Vessels Are Scored",
        "desc": "How the Taiwan Gray Zone Monitor scores suspicious vessels: data sources, the 8-criterion threat-scoring engine, vessel-type weighting, maritime-zone scoring, and limitations.",
        "kw": "AIS analysis, threat scoring, CSIS methodology, OSINT, GFW SAR, submarine cable monitoring",
    },
    "blog-what-is-ais.html": {
        "title": f"What Is AIS? Why It Matters for Submarine-Cable Security | {SITE}",
        "social": "What Is AIS? Why It Matters for Cable Security",
        "desc": "AIS is a ship's digital passport. Learn how AIS works, why vessels switch it off to 'go dark', how SAR satellites find them, and the link to submarine-cable security.",
        "kw": "AIS, automatic identification system, dark vessel, SAR satellite, MMSI, going dark, vessel tracking",
    },
    "blog-what-is-submarine-cable.html": {
        "title": f"What Is a Submarine Cable? Why It Matters | {SITE}",
        "social": "What Is a Submarine Cable? Why It Matters",
        "desc": "Submarine cables carry over 95% of international internet traffic. Learn how they work, how they are laid and repaired, and why they are Taiwan's digital lifeline.",
        "kw": "submarine cable, fiber optic, internet infrastructure, Taiwan, cable laying, cable repair",
    },
    "blog-cable-threats.html": {
        "title": f"Threats to Submarine Cables: Shadow Fleets & Gray-Zone Sabotage | {SITE}",
        "social": "Threats to Submarine Cables",
        "desc": "Natural, accidental, and deliberate threats to submarine cables — shadow fleets, anchor dragging vs sabotage, and why it is the perfect gray-zone tactic near Taiwan.",
        "kw": "submarine cable threats, shadow fleet, gray zone warfare, anchor dragging, sabotage, Taiwan",
    },
    "blog-taiwan-cable-status.html": {
        "title": f"Taiwan's Submarine Cable Situation | {SITE}",
        "social": "Taiwan's Submarine Cable Situation",
        "desc": "Taiwan's strategic cable density, landing stations, outer-island vulnerability, break trends, repair challenges, and government resilience efforts.",
        "kw": "Taiwan submarine cable, Matsu cable, landing station, cable break, resilience",
    },
    "blog-taiwan-enforcement.html": {
        "title": f"Taiwan's Enforcement Framework & Challenges | {SITE}",
        "social": "Taiwan's Enforcement Framework & Challenges",
        "desc": "Taiwan's legal toolbox for cable protection, the flag-state jurisdiction gap, why perpetrators are rarely prosecuted, and how other countries are responding.",
        "kw": "submarine cable law, UNCLOS, flag state jurisdiction, gray zone enforcement, Taiwan",
    },
    "blog-what-is-dark-vessel.html": {
        "title": f"What Is a Dark Vessel? SAR vs AIS Detection Near Taiwan | {SITE}",
        "social": "What Is a Dark Vessel?",
        "desc": "A dark vessel is a ship that turns off AIS but is still detected by SAR satellites. Learn how SAR and AIS are cross-referenced and why dark vessels matter around Taiwan.",
        "kw": "dark vessel, AIS off, SAR satellite detection, Taiwan Strait, Global Fishing Watch, maritime gray zone",
    },
    "blog-what-is-ship-to-ship-transfer.html": {
        "title": f"What Is a Ship-to-Ship (STS) Transfer? How It's Detected | {SITE}",
        "social": "What Is a Ship-to-Ship (STS) Transfer?",
        "desc": "An STS transfer is two ships moving cargo alongside at sea. Learn how STS is detected, lawful pair-trawling vs suspicious transfers, and the link to sanctions evasion.",
        "kw": "ship-to-ship transfer, STS, rendezvous, pair trawling, sanctions evasion, shadow fleet, Taiwan Strait",
    },
    "blog-what-is-ais-spoofing.html": {
        "title": f"What Is AIS Spoofing? Detecting Fake Positions & Identity Fraud | {SITE}",
        "social": "What Is AIS Spoofing?",
        "desc": "AIS spoofing is a vessel broadcasting a false position, speed, or another ship's identity. Learn how it differs from going dark, how it is detected (impossible speed, box/circle tracks, registry mismatch), and why it matters near Taiwan.",
        "kw": "AIS spoofing, fake GPS position, identity fraud, MMSI, IMO, impossible speed, submarine cable, gray zone, Taiwan Strait",
    },
    "blog-what-is-maritime-gray-zone.html": {
        "title": f"What Is Maritime Gray-Zone Activity? Tactics Around Taiwan | {SITE}",
        "social": "What Is Maritime Gray-Zone Activity?",
        "desc": "Maritime gray-zone activity is coercion kept below the threshold of open conflict. An overview of dark vessels, AIS spoofing, ship-to-ship transfers, shadow fleets, and cable threats — and how OSINT tracks them around Taiwan.",
        "kw": "maritime gray zone, gray zone operations, dark vessel, AIS spoofing, ship-to-ship transfer, shadow fleet, submarine cable, Taiwan Strait, OSINT",
    },
    "blog-what-is-shadow-fleet.html": {
        "title": f"What Is a Shadow Fleet? Sanctions Evasion, STS & Flags of Convenience | {SITE}",
        "social": "What Is a Shadow Fleet?",
        "desc": "A shadow fleet is a group of vessels that hide ownership and identity to evade sanctions and inspection — through flags of convenience, going dark and AIS spoofing, ship-to-ship transfers, and shell companies. How it operates and why it matters for Taiwan's submarine cables.",
        "kw": "shadow fleet, dark fleet, sanctions evasion, flag of convenience, ship-to-ship transfer, AIS spoofing, going dark, submarine cable, Taiwan Strait, OSINT",
    },
    "blog-what-is-maritime-sanctions.html": {
        "title": f"What Are Maritime Sanctions? OFAC / EU / UN Lists, Shadow Fleet & IMO Matching | {SITE}",
        "social": "What Are Maritime Sanctions?",
        "desc": "Maritime sanctions are restrictions governments and international bodies place on specific ships, owners, and companies. How the OFAC (U.S.), EU, UK, UN, and UANI lists designate vessels by IMO number, how sanctioned tankers evade via flag hopping, name changes, and fake IMOs, and why sanctioned ships appear in Taiwan's waters.",
        "kw": "maritime sanctions, OFAC, SDN list, EU sanctions, UN sanctions, UANI, shadow fleet, flag of convenience, IMO number, tanker, Taiwan Strait, OSINT",
    },
    "blog-taiwan-maritime-zones.html": {
        "title": f"Taiwan's Maritime Zones: Baseline, Territorial Sea, Contiguous Zone & EEZ | {SITE}",
        "social": "Taiwan's Maritime Zones Explained",
        "desc": "Territorial baseline, 12 nm territorial sea, 24 nm contiguous zone, and 200 nm EEZ — what each maritime zone means under UNCLOS, how this site's baseline / 12 nm / 24 nm map layers are used, and the difference between legal delimitation and a risk-analysis aid.",
        "kw": "territorial baseline, territorial sea, contiguous zone, EEZ, UNCLOS, maritime zones, Taiwan, 12 nautical miles, 24 nautical miles, 200 nautical miles",
    },
    "blog-gray-zone-glossary.html": {
        "title": f"Taiwan Maritime Gray-Zone Glossary (Bilingual) | {SITE}",
        "social": "Taiwan Maritime Gray-Zone Glossary (Bilingual)",
        "desc": "Bilingual (English/Chinese) definitions of maritime gray-zone, OSINT, AIS/SAR, and law-of-the-sea terms: dark vessel, AIS spoofing, MMSI, STS, SAR, flag of convenience, territorial baseline.",
        "kw": "gray zone glossary, dark vessel, AIS spoofing, MMSI, ship-to-ship transfer, SAR, flag of convenience, territorial baseline",
    },
    "blog-ocean-law-maritime-rights.html": {
        "title": f"What Are \"Maritime Rights\"? Sovereignty, Sovereign Rights & Jurisdiction | {SITE}",
        "social": "What Are \"Maritime Rights\"?",
        "desc": "Maritime rights under Taiwan's Ocean Basic Act are a three-tier structure — sovereignty, sovereign rights, and jurisdiction — running from the territorial sea to the high seas, mapping onto national security, maritime law-and-order, and maritime safety. Why submarine cables are critical national infrastructure.",
        "kw": "maritime rights, law of the sea, sovereignty, sovereign rights, jurisdiction, UNCLOS, Ocean Basic Act, submarine cable, critical infrastructure, Taiwan",
    },
    "blog-ocean-law-cable-protection.html": {
        "title": f"Submarine Cables Under International Law: From the 1884 Convention to UNCLOS | {SITE}",
        "social": "Submarine Cables Under International Law",
        "desc": "How international law protects submarine cables — the 1884 Convention, the 1958 High Seas Convention, and UNCLOS Article 113 — and the fatal gap between prescriptive and enforcement jurisdiction under exclusive flag-state jurisdiction.",
        "kw": "submarine cable, international law, 1884 Convention, UNCLOS, Article 113, High Seas Convention, flag state jurisdiction, Lotus case, prescriptive jurisdiction, enforcement jurisdiction",
    },
    "blog-ocean-law-enforcement-jurisdiction.html": {
        "title": f"Who Can Board and Enforce? Hot Pursuit, Right of Visit & Universal Jurisdiction | {SITE}",
        "social": "Who Can Board and Enforce?",
        "desc": "The enforcement toolkit for cable protection: UNCLOS right of visit (Art. 110) and hot pursuit (Art. 111), coastal-state protective powers, Taiwan's Telecommunications Management Act and Coast Guard Act, and the proposal to extend universal jurisdiction to malicious cable-cutting.",
        "kw": "hot pursuit, right of visit, universal jurisdiction, UNCLOS Article 110, Article 111, Telecommunications Management Act, Coast Guard Act, submarine cable enforcement, extraterritorial jurisdiction",
    },
    "blog-taiwan-cable-infrastructure.html": {
        "title": f"Taiwan's Submarine Cable Infrastructure: 4 Landing Stations & 12 Cable Systems | {SITE}",
        "social": "Taiwan's Submarine Cable Infrastructure",
        "desc": "The physical geography of Taiwan's submarine cables: the Toucheng, Tamsui, Bali, and Fangshan landing stations, ~12 international cable systems, the Kinmen–Xiamen and Tamsui–Fuzhou cross-strait cables, and the fragility of outer-island redundancy.",
        "kw": "Taiwan submarine cable, landing station, Toucheng, Tamsui, Bali, Fangshan, international cable, Kinmen-Xiamen cable, Tamsui-Fuzhou, backup, cable resilience",
    },
    "blog-global-cable-industry.html": {
        "title": f"The Global Submarine Cable Industry & Why Cables Break | {SITE}",
        "social": "The Global Submarine Cable Industry",
        "desc": "An overview of the global submarine cable industry: 400+ cables and over 1.1 million km, consortium vs private cables, the OTT investment shift (Google, Meta, Microsoft, Amazon), fault causes (over 70% human — fishing and anchors), repair challenges, AIS protection, and cable geopolitics.",
        "kw": "submarine cable industry, global cables, cable map, OTT cables, Google cable, cable faults, fishing, anchor, cable ship, AIS, undersea great wall, Belt and Road",
    },
}

# English FAQPage content per page. The source pages embed a FAQPage JSON-LD
# block written in Chinese; for the /en/ mirror we swap `mainEntity` for these
# English (question, answer) pairs so the schema's language matches the page's
# declared `inLanguage:"en"` and the English visible content. Keep each list in
# the same order as the Chinese source so the FAQ stays a true translation.
EN_FAQ = {
    "intro.html": [
        ("What is a “dark vessel”?",
         "A dark vessel is a ship that has switched off its AIS (Automatic Identification System) signal and so cannot be seen by ordinary vessel-tracking systems. This site detects such ships through SAR (Synthetic Aperture Radar) satellite imagery and cross-references them with AIS data to find suspicious targets that have deliberately gone dark."),
        ("Where does the data come from?",
         "Vessel position data comes from Taiwan Port Bureau's public AIS feed; dark-vessel detection comes from Global Fishing Watch's SAR satellite imagery; and the threat-scoring methodology draws on the gray-zone analysis in the US think tank CSIS's “Signals in the Swarm” report. All data is open-source intelligence (OSINT)."),
        ("How often is the data updated?",
         "AIS vessel data updates automatically every 2 hours; the full data pipeline (dark-vessel detection, threat scoring, and track analysis) runs every 12 hours; and statistical reports are compiled weekly. All times are shown in UTC (Coordinated Universal Time)."),
        ("How is a vessel judged “suspicious”?",
         "The site uses an 8-criterion weighted scoring system, including loitering at low speed near submarine cables, anomalous zigzag navigation, frequent changes of ship name or call sign (identity manipulation), going dark for long periods, AIS position spoofing, and ship-to-ship (STS) transfers. Vessels that reach the threshold are flagged as suspicious and listed."),
        ("Why do submarine cables matter?",
         "Over 95% of Taiwan's external internet communication depends on submarine cables, which run densely through the Taiwan Strait. Trawler anchoring and operations are a leading cause of cable damage; when unidentified ships loiter near cable routes for long periods it becomes a threat to infrastructure security."),
        ("Is this an official government website?",
         "No. This is an independent open-source intelligence (OSINT) project. All data comes from public sources, and the analysis methods and code are published on GitHub. It does not represent the position of any government or official body."),
    ],
    "research-submarine-cable-legal.html": [
        ("How does UNCLOS regulate damage to submarine cables?",
         "Article 113 of the UN Convention on the Law of the Sea (UNCLOS) requires states parties to enact domestic laws penalizing intentional or negligent damage to submarine cables on the high seas. However, Articles 92 and 94 provide that ships on the high seas are subject only to the jurisdiction of their flag state, and third states have no power to enforce against them. So even when a foreign ship is found damaging a cable, the victim state cannot directly seize or punish it and must rely on the flag state's cooperation — and flag states often lack the motive or capacity to impose effective sanctions, creating a major enforcement gap."),
        ("Does the 1884 Submarine Cable Protection Convention protect Taiwan?",
         "Article 2(1) of the 1884 Convention for the Protection of Submarine Cables makes intentional damage a criminal offense, and Article 8(2) gives the victim state a legal basis to exercise “supplementary jurisdiction” when the flag state fails to fulfill its jurisdictional duty. In theory this is a stronger enforcement tool than UNCLOS. However, the convention currently has only 36 signatory states; neither Taiwan nor the People's Republic of China is a party, so it does not legally apply to cable-damage incidents in the Taiwan Strait."),
        ("What is “civilian-cover lawfare”?",
         "“Civilian-cover lawfare” is a core technique of the CCP's gray-zone strategy. By registering ships under third-country flags (such as Tanzania, Togo, or Cameroon) as “flags of convenience,” the CCP uses the cover of a civilian fishing or merchant identity to conduct cable-damaging operations. Because UNCLOS subjects high-seas ships only to flag-state jurisdiction, victim states cannot enforce against these flag-of-convenience vessels, letting the CCP damage infrastructure under the protection of the legal framework while keeping “plausible deniability.”"),
        ("How does the “shadow fleet” evade tracking?",
         "Shadow fleets evade tracking in several ways: frequently changing ship names and flag registration so no continuous identity record can be built; switching off the AIS system so vessels “disappear” from routine monitoring; hiding the real owner behind layers of shell companies; and conducting ship-to-ship transfers to avoid entering port for inspection. The “Shunxing 39,” which cut the Trans-Pacific Express cable off Taiwan in January 2025, sailed under a Tanzanian flag, was owned by a Hong Kong company, and had its AIS off at the time — a perfect embodiment of this evasion system."),
        ("What domestic laws can Taiwan apply?",
         "The 2023 amendment to Article 72 of the Telecommunications Management Act makes “damaging cable landing stations and their connecting lines” a criminal offense, providing a domestic-law basis against cable damage. However, the law's reach is mainly limited to Taiwan's territorial sea and exclusive economic zone; for foreign ships operating on the high seas or in other states' waters, Taiwan's enforcement power is still constrained by the international-law framework. Criminal-code offenses of property damage and endangering public traffic safety may also apply, but face the same extraterritorial-jurisdiction challenge."),
        ("What international cooperation frameworks exist?",
         "Several frameworks already exist: the 2024 New York Joint Statement on the Security and Resilience of Undersea Cables, signed by 17 states, pledges stronger cooperation on cable protection; the QUAD Partnership for Cable Connectivity strengthens cable security in the Indo-Pacific; the G7 shadow-fleet working group coordinates tracking and sanctions against sanctions-evading fleets; IMO Resolution A.1192(33) calls on states to strengthen ship-registration management and flag-state responsibility; and the Tokyo MOU provides a regional mechanism for port-state inspection. Taiwan can indirectly strengthen its cable-protection capacity by joining these frameworks through allies."),
    ],
    "blog-methodology.html": [
        ("Where does the site's data come from?",
         "Two main sources, cross-referenced: AIS data from Taiwan's Port Bureau (updated every 2 hours) and SAR satellite dark-vessel detections from Global Fishing Watch."),
        ("Does a high threat score mean a vessel is guilty?",
         "No. A high score means the behavior pattern is anomalous, not that hostile intent is confirmed. The system is designed to support human analysis, not to replace professional intelligence assessment."),
        ("How many criteria does the scoring system use?",
         "Eight, including cable proximity, low-speed loitering, zigzag navigation, AIS anomalies and spoofing, sanctions-list matching, and ship-to-ship transfers, weighted by vessel type."),
        ("Is this project open source?",
         "Yes. All code is published on GitHub under the MIT license, so anyone can inspect the scoring logic and data processing and propose improvements."),
    ],
    "blog-what-is-ais.html": [
        ("Is AIS mandatory?",
         "Under the IMO's SOLAS convention, ocean-going ships above 300 gross tons and all passenger ships must install and operate AIS, broadcasting vessel information every few seconds."),
        ("Is switching off AIS illegal?",
         "Not necessarily. In some legitimate situations (such as piracy-prone areas) a captain has reason to switch off AIS. But going dark for no reason near submarine cables in the waters around Taiwan is a strong warning sign."),
        ("How do you track a “dark vessel” that shows no AIS signal?",
         "Synthetic Aperture Radar (SAR) satellites detect metal objects on the sea surface, which are then cross-referenced with AIS data. A ship picked up by SAR but with no AIS signal is flagged as a dark vessel."),
        ("What is AIS spoofing?",
         "A vessel broadcasting false position coordinates, a fake speed, or even impersonating another ship's identity to hide its true movements. It is one of the patterns this site's threat-scoring engine specifically detects."),
    ],
    "blog-what-is-submarine-cable.html": [
        ("Do submarine cables really carry most of the world's internet traffic?",
         "Yes. Over 95% of international internet traffic travels through submarine cables, with satellites carrying only a tiny share. Almost every international phone call, cross-border financial transaction, and overseas web page load uses a submarine cable."),
        ("Why are submarine cables so thin yet so important?",
         "Deep-sea cable is only about 17 mm in diameter, but a single fiber can carry tens of Tbps; a whole cable contains dozens of fiber pairs with hundreds of Tbps of total capacity — equivalent to streaming millions of 4K movies at once."),
        ("How long does a submarine cable last?",
         "A submarine cable is typically designed to last about 25 years, but it can be damaged early by seabed earthquakes, fishing trawls, or ship anchors, requiring a specialized cable-repair ship to fix."),
        ("How many submarine cables does Taiwan have?",
         "Taiwan's main island has 14 international submarine cables and about 10 domestic cables (connecting outlying islands). The outlying islands are extremely vulnerable: Matsu has only 2 cables, and Kinmen just 1 plus 1 backup."),
    ],
    "blog-cable-threats.html": [
        ("What is the biggest threat to submarine cables?",
         "Statistically, 41% of faults come from fishing trawls and 16% from anchors, mostly accidental; but deliberate gray-zone sabotage is the hardest to attribute and is the core concern this site monitors."),
        ("How can you tell an accident from deliberate sabotage?",
         "Deliberate sabotage is often disguised as an accident — using civilian fishing or cargo ships, acting late at night or in deep water, and switching off AIS beforehand to make it hard to distinguish from a genuine accident."),
        ("What is a “shadow fleet”?",
         "Vessels with unclear or frequently changed flags, complex ownership structures, and anomalous AIS records. Originally used to evade Russian oil sanctions, they have recently also appeared near submarine cables."),
        ("Does China have the ability to cut deep-sea cables?",
         "According to research reports, Chinese research institutions have obtained patents for deep-sea cutting equipment that can sever cables in deep water far from any normal fishing need, widening the room for “pretend accident” operations."),
    ],
    "blog-taiwan-cable-status.html": [
        ("Why is a Matsu outage especially serious?",
         "The Matsu islands rely on just 2 submarine cables to the Taiwan mainland. In February 2023 both were cut by Chinese ships within 6 days, leaving 14,000 residents without internet for about 50 days."),
        ("How long does it take to repair a broken cable?",
         "Depending on water depth and damage, usually 14 to 60 days. The 2023 Matsu break took about 50 days, because there are only around 60 specialized cable-repair ships worldwide and they often have to queue."),
        ("Is Taiwan's cable-break frequency normal?",
         "There are roughly 100–200 cable breaks worldwide each year, but the rate around Taiwan is disproportionately high for geopolitical reasons. There were 12 in 2023, and 4 in January–February 2025 alone."),
        ("How many international and domestic cables does Taiwan have?",
         "Taiwan's main island has 14 international submarine cables and about 10 domestic cables, the latter connecting outlying islands such as Matsu, Kinmen, and Penghu."),
    ],
    "blog-taiwan-enforcement.html": [
        ("Does Taiwan have laws to punish cutting submarine cables?",
         "Yes. The 2023 amendment to Article 72 of the Telecommunications Management Act makes intentionally damaging a submarine cable punishable by up to 3 years' imprisonment, detention, or a fine of up to NT$2 million."),
        ("Why are offending vessels rarely prosecuted?",
         "Under Article 92 of UNCLOS, ships on the high seas are under the exclusive jurisdiction of their flag state. Taiwan struggles to board, inspect, or prosecute foreign vessels, and offenders often hide behind shell companies."),
        ("Does the 1884 Submarine Cable Convention protect Taiwan?",
         "No. Because of its diplomatic status Taiwan is not a party, and China refuses to recognize the convention's binding force, leaving this 19th-century treaty effectively useless in the Taiwan Strait context."),
        ("How is Taiwan responding?",
         "By building a blacklist of more than 96 suspicious ships, conducting informal maritime cooperation with friendly nations, joining the G7 shadow-fleet framework, and building international pressure by publishing suspicious AIS tracks."),
    ],
    "blog-what-is-dark-vessel.html": [
        ("What is a dark vessel?",
         "A dark vessel is a ship detected by Synthetic Aperture Radar (SAR) satellites but with no matching AIS signal. In other words, it is physically at sea and picked up by radar, yet “invisible” on ordinary AIS tracking platforms."),
        ("Does a ship disappear once it switches off AIS?",
         "No. AIS is only a radio signal the ship broadcasts itself; switching it off does not remove the ship from radar. SAR satellites can still detect the metal hull on the surface, regardless of weather, cloud, or day and night."),
        ("How are dark vessels detected?",
         "SAR satellites scan the sea surface for vessel positions, which are cross-referenced with AIS data from the same period. A target detected by SAR with no AIS match is flagged as a dark vessel. This site uses SAR detections from Global Fishing Watch."),
        ("Does being detected as a dark vessel mean it is illegal?",
         "Not necessarily. A SAR detection just means a radar-visible target — it could be a fishing boat, cargo ship, or government vessel; and switching off AIS is legal in some situations. A dark vessel is a lead worth investigating, not a finding of wrongdoing."),
        ("Why does dark-vessel activity around Taiwan matter?",
         "Taiwan depends heavily on submarine cables for external connectivity, and cable-damage incidents often show a “switch off AIS before approaching the cable” pattern. A dark vessel that appears near a cable route and loiters at low speed is exactly the behavior this site's threat scoring watches for."),
    ],
    "blog-what-is-ship-to-ship-transfer.html": [
        ("What is a ship-to-ship (STS) transfer?",
         "A ship-to-ship transfer (STS) is when two vessels come alongside each other at sea to move cargo, fuel, or catch from one ship to another, without needing to enter port."),
        ("How is an STS transfer detected?",
         "By spatiotemporal matching of AIS positions: finding two ships that are very close together (within about 10 meters) during the same period and stay that way for a while is flagged as an STS event. This site updates detections every 2 hours."),
        ("Is an STS transfer always illegal?",
         "No. Many STS operations are lawful — for example pair trawling (two boats towing one net together), resupply at sea, refueling, or waiting outside port. Whether it is suspicious depends on vessel type, location, whether AIS is switched off, and whether sanctioned or identity-anomalous ships are involved."),
        ("Why is STS linked to the gray zone and sanctions evasion?",
         "Sanctioned states' “shadow fleets” often use STS at sea to transfer oil and other cargo, avoiding port inspections and cutting the trail of where the cargo came from. Combined with switching off AIS, STS becomes a key method for hiding the flow of goods."),
        ("How does this site interpret STS events?",
         "The site distinguishes “pair trawling” from “suspicious STS,” and folds transfers that involve sanctions, identity changes, or going dark in sensitive waters into its threat scoring — as a lead for human analysis, not a finding of wrongdoing."),
    ],
    "blog-what-is-ais-spoofing.html": [
        ("How does AIS spoofing differ from switching AIS off?",
         "Switching off AIS (going dark) makes a ship vanish from AIS; AIS spoofing keeps broadcasting, but the content is fake — a false position, a false speed, or even impersonating another ship's identity. Going dark leaves a blank; spoofing actively creates misdirection."),
        ("What AIS information can be faked?",
         "Almost everything: GPS position, speed over ground (SOG), course over ground (COG), MMSI, ship name, call sign, and IMO number. Because AIS is filled in and broadcast by the ship itself, there is no real-time third-party verification."),
        ("How is AIS spoofing detected?",
         "Through internal contradictions in physics and data: impossible speed (distance between two points divided by time exceeding a reasonable limit), a broadcast course that does not match the actual direction of movement, artificial box- or circle-shaped tracks, and a ship name or IMO that does not match registry data such as the ITU. This site's scoring engine specifically detects these patterns."),
        ("What does AIS spoofing have to do with submarine cables?",
         "If a ship can make itself appear to be elsewhere, it can operate near a cable without being immediately linked to it. Combined with going dark and identity changes, spoofing makes gray-zone behavior even harder to attribute."),
    ],
    "blog-what-is-shadow-fleet.html": [
        ("What is a shadow fleet?",
         "A shadow fleet (also called a dark fleet) is a group of vessels that deliberately hide their true owner and identity to evade sanctions and inspection. They typically register under flags of convenience, frequently change ship names and flags, are held through layers of shell companies, and mask cargo and movements at sea by going dark, spoofing AIS, and conducting ship-to-ship transfers."),
        ("What methods do shadow fleets use to evade tracking?",
         "Common methods include registering under loosely regulated flags of convenience; frequently changing name, call sign, or MMSI; switching off or spoofing AIS; using ship-to-ship (STS) transfers to move cargo at sea and avoid port inspection; and hiding the real owner behind shell companies and questionable insurance."),
        ("What does a shadow fleet have to do with submarine cables?",
         "Shadow fleets were originally used to evade oil sanctions, but the same capability — hidden identity, going dark, and hard-to-attribute operations — also makes them an ideal vehicle for gray-zone threats to submarine cables: operating near a cable and dragging an anchor, then being hard to hold accountable afterward."),
        ("Does being labeled a shadow fleet vessel mean it is illegal?",
         "Not necessarily. Flags of convenience, transfers at sea, and name changes are lawful shipping practices in many situations. A shadow fleet is a risk pattern formed by a combination of anomalies — a lead worth investigating, not a finding of wrongdoing."),
    ],
    "blog-what-is-maritime-sanctions.html": [
        ("What are maritime sanctions?",
         "Maritime sanctions are restrictions that governments and international bodies place on specific ships, owners, and shipping or insurance companies to cut off a target's shipping and money flows. A designated vessel is usually listed by its IMO number on public lists, and companies are barred from trading with it or providing insurance and port services."),
        ("Who can sanction a ship, and what are the lists?",
         "The main ones are the U.S. Treasury's OFAC (SDN list), the EU, the UK's OFSI, the UN Security Council, Switzerland's SECO, and New Zealand's MFAT, plus NGO initiatives like UANI. One ship can be designated by several bodies at once; platforms like OpenSanctions consolidate these lists for lookup."),
        ("How do sanctioned ships evade?",
         "Common tactics are re-flagging to loosely regulated flags of convenience, frequently changing names, broadcasting fake or invalid IMO numbers, switching off AIS in sensitive waters, and ship-to-ship (STS) transfers at sea to avoid port inspection. Because the IMO number is meant to be permanent, matching by IMO is most reliable — which is exactly the identifier ships most want to evade."),
        ("Why are sanctioned ships visible in Taiwan's waters?",
         "The Taiwan Strait and its surroundings are a major East Asian shipping lane and cable hub with dense traffic. Sanctioned shadow-fleet tankers transit, wait, or transfer cargo here. Matching public sanctions lists against live AIS reveals several OFAC/EU/UANI-designated tankers inside the monitored area — not speculation, but the result of exact IMO matching."),
    ],
    "blog-taiwan-maritime-zones.html": [
        ("What is a territorial baseline?",
         "The territorial baseline is the line from which each zone's width is measured. It usually follows the coast's low-water line (the normal baseline), or joins appropriate points as straight baselines where the coast is broken or has offshore islands. Every zone's width is measured seaward from this baseline."),
        ("What's the difference between the territorial sea, contiguous zone, and EEZ?",
         "The territorial sea (12 nm) is under the coastal state's sovereignty; within the contiguous zone (24 nm) the state may exercise the control needed over customs, fiscal, immigration, and sanitary matters; the EEZ (200 nm) gives the state sovereign rights over natural resources, while other states keep the freedoms of navigation and cable-laying."),
        ("Are the zones on this site's map official boundaries?",
         "No. The baseline and 12/24 nm bands are drawn approximately from public data for visualization and risk analysis. They are not an official legal delimitation and imply no sovereignty or border claim."),
        ("How do maritime zones relate to gray-zone monitoring?",
         "Which zone a vessel is in shapes the legal meaning and suspiciousness of its behavior. A foreign ship loitering inside the territorial sea, or lingering anomalously in the contiguous zone or near a cable route, warrants more caution than one transiting the high seas. This site overlays vessels with the zones as a supporting lead for threat assessment."),
    ],
    "blog-what-is-maritime-gray-zone.html": [
        ("What is maritime gray-zone activity?",
         "Gray-zone activity is coercion or provocation deliberately kept below the threshold of open armed conflict, using ambiguity and deniability to achieve its aims. At sea, common forms include fishing-vessel swarms, switching off and spoofing AIS, ship-to-ship transfers, shadow-fleet transshipment, and threats to submarine cables."),
        ("What are the common maritime gray-zone tactics?",
         "Dark vessels (AIS off but caught by SAR), AIS spoofing (broadcasting fake positions or stolen identities), STS transfers (ship-to-ship transfers at sea), shadow fleets (sanctions-evading tankers), fishing-vessel swarms, and anomalous loitering or anchor dragging near submarine cables."),
        ("Why does Taiwan pay special attention to the maritime gray zone?",
         "Taiwan is an island that relies on submarine cables for over 95% of its external communications, and it sits where the busy Taiwan Strait meets major fishing grounds. This lets gray-zone tactics blend into normal maritime activity while potentially causing disproportionate impact."),
        ("How does OSINT observe the gray zone?",
         "By cross-referencing AIS positions, SAR satellite dark-vessel detections, identity changes, and STS detection, then scoring and ranking anomalous behavior. The score is a risk ranking and a lead to investigate, not a legal finding."),
    ],
    "blog-ocean-law-maritime-rights.html": [
        ("What are \"maritime rights\"?",
         "\"Maritime rights\" is the term used in Taiwan's Ocean Basic Act for the rights and interests a state enjoys and may assert in maritime zones under international law (above all UNCLOS). It is not a single right but three tiers of differing strength — sovereignty, sovereign rights, and jurisdiction — weakening from the coast outward. Cable protection sits inside this structure."),
        ("What's the difference between sovereignty, sovereign rights, and jurisdiction?",
         "They differ in strength: sovereignty is the most complete power, reaching internal waters and the territorial sea, where the state governs fully; sovereign rights are exclusive rights over specific matters (such as exploring and exploiting EEZ natural resources) but do not reach freedoms like navigation; jurisdiction is management power over specific functions (marine research, environmental protection, artificial islands). The farther offshore, the weaker the tier a state can assert."),
        ("Why are submarine cables \"critical national infrastructure\"?",
         "Over 95% of Taiwan's external internet and communications depend on submarine cables, on which finance, government, defense, and civilian communications are built. Cutting a cable affects not just connectivity but the functioning of the whole society and national security. Cables are therefore treated as critical national infrastructure, with security spanning national security, maritime law-and-order, and maritime safety."),
        ("What does Taiwan's Ocean Basic Act mean for cable protection?",
         "The Ocean Basic Act is a policy-framework parent law that declares basic principles for safeguarding maritime rights, maritime security, and ocean resources, but does not itself set criminal penalties. It provides the legal basis for folding cable security into the overall \"maritime rights\" framework, which individual laws such as the Telecommunications Management Act and the Coast Guard Act then implement with concrete controls and enforcement."),
    ],
    "blog-ocean-law-cable-protection.html": [
        ("How does the 1884 Convention protect submarine cables?",
         "The 1884 Convention for the Protection of Submarine Cables is the earliest treaty dedicated to cables. Art. 2 makes intentional or negligent damage punishable; Art. 8 assigns jurisdiction to the flag state and allows the victim state to exercise supplementary jurisdiction where the flag state fails to act; Art. 10 gives warships a right to inspect suspect vessels. It is in theory more toothed than UNCLOS, but has only about 36 parties — and neither Taiwan nor the PRC is one — so it is hard to apply directly in the Strait."),
        ("What does UNCLOS Article 113 provide?",
         "UNCLOS Art. 113 requires each party to enact domestic law making intentional or grossly negligent damage to high-seas submarine cables a punishable offense. It imposes a \"duty to legislate\" but allocates only prescriptive jurisdiction — it does not also grant states enforcement jurisdiction over foreign ships on the high seas. That is the core gap in international cable protection."),
        ("What is the \"prescriptive vs enforcement\" jurisdiction gap?",
         "Prescriptive jurisdiction is the power to make law and declare conduct illegal; enforcement jurisdiction is the power to actually board, seize, arrest, and prosecute. UNCLOS Art. 113 requires states to legislate against cable damage, yet Arts. 92 and 97 hand high-seas ships to exclusive flag-state jurisdiction. The result: the victim state has a law but no power to act against the foreign ship — a \"law without enforcement\" gap."),
        ("Why is flag-state jurisdiction the biggest institutional gap?",
         "Under the principle established from the Permanent Court's 1927 Lotus case and codified in UNCLOS Art. 92, a ship on the high seas is in principle subject only to its flag state. If perpetrators register under a loosely regulated flag of convenience, the victim state has no power to board or prosecute and must rely on the flag state's cooperation — which is often unwilling or unable, rendering protection hollow."),
    ],
    "blog-ocean-law-enforcement-jurisdiction.html": [
        ("What is the right of visit?",
         "The right of visit (UNCLOS Art. 110) lets a warship inspect a foreign ship on the high seas, but only in specific cases: reasonable suspicion of piracy, the slave trade, unauthorized broadcasting, or that the ship is stateless or falsely flagged (e.g. actually of the warship's nationality). Inspection usually means demanding the ship's papers and, if needed, boarding. Unfortunately \"submarine cable damage\" is not among Art. 110's listed grounds — a major limit on cable enforcement."),
        ("What is hot pursuit?",
         "Hot pursuit (UNCLOS Art. 111) lets a coastal state, when it has good reason to believe a foreign ship has broken its laws in internal waters, the territorial sea, the contiguous zone, or the EEZ, pursue that ship from those waters in an uninterrupted chase onto the high seas and intercept it. But the pursuit must be \"uninterrupted\" and ends once the ship enters its own or a third state's territorial sea — in practice a poor fit for an elusive cable-cutter."),
        ("Can Telecommunications Management Act Article 72 be enforced against foreign ships?",
         "Art. 72 (2023 amendment) criminalizes intentional cable damage and is Taiwan's domestic basis against cable cuts. But its reach is mainly Taiwan's territorial sea and EEZ; against foreign ships operating on the high seas or in other states' waters it remains constrained by exclusive flag-state jurisdiction, creating an extraterritorial-application problem. Coast Guard Act Arts. 4-5 grant enforcement and inspection powers within waters under Taiwan's jurisdiction."),
        ("Why do some argue for extending universal jurisdiction to cables?",
         "Universal jurisdiction currently applies mainly to \"enemies of all humankind\" such as piracy and the slave trade, which any state may prosecute. Because submarine cables are globally shared critical infrastructure and deliberate damage harms a transnational public interest, some scholars propose folding malicious cable-cutting into universal jurisdiction by analogy to piracy, so any state could board and prosecute and fill the gap left by flag-state jurisdiction. This is still a developing proposal, not yet positive international law."),
    ],
    "blog-taiwan-cable-infrastructure.html": [
        ("What are Taiwan's cable landing stations?",
         "Taiwan's international cables land mainly at four stations: Toucheng, Tamsui, and Bali in the north, and Fangshan in the south. After landfall, cables are carried by terrestrial backhaul to network hubs and data centers in places like Taipei and Neihu. The heavy concentration of landing stations is one of Taiwan's structural cable-resilience weaknesses."),
        ("How many submarine cables does Taiwan have?",
         "Taiwan's main island has about 14 international submarine cable systems plus roughly 10 domestic cables (connecting outer islands such as Penghu, Kinmen, and Matsu). About 12 international cable systems land on the main island across the four stations of Toucheng, Tamsui, Bali, and Fangshan. The outer islands have very few cables and are the most fragile link."),
        ("What are the Kinmen–Xiamen and Tamsui–Fuzhou cables?",
         "The Kinmen–Xiamen cable is a short cable (about 21 km) linking Kinmen to Xiamen in China, reflecting Kinmen's peculiar geographic and communications situation. The Tamsui–Fuzhou cable (TSE) is a system about 260 km long. These cross-strait cables are geopolitically sensitive."),
        ("Why is outer-island cable redundancy especially fragile?",
         "Outer islands such as Matsu rely on just 2 cables to the main island, and Kinmen has only 1 plus 1 backup. If those few cables are damaged together, an entire island can be cut off for weeks — in February 2023 Matsu's two cables were cut within 6 days and about 14,000 residents' internet was affected for nearly 50 days. The main island has more redundancy, but landing-station concentration remains a concern."),
    ],
    "blog-global-cable-industry.html": [
        ("How many submarine cables are there worldwide?",
         "By public cable-map counts, there are roughly 400-plus live and planned submarine cable systems globally, totaling over 1.1 million km — enough to circle the Earth dozens of times. They carry over 95% of international data traffic and are the true backbone of the internet. The figures change continuously as systems are built and retired."),
        ("Who owns these cables?",
         "Traditionally cables were built by consortia of telecom operators co-investing and sharing capacity. The biggest recent shift is OTT content giants — Google, Meta, Microsoft, Amazon — heavily investing in and even solely building \"private cables\" to feed the huge traffic between their cloud and data centers. Cable ownership is moving from telecoms to tech giants."),
        ("What most commonly breaks a submarine cable?",
         "Statistically the vast majority are human accidents: fishing activity (trawling) about 38% and ship anchors about 25%, together over 70% human; natural and environmental causes (seabed quakes, current abrasion) about 14%. Deliberate sabotage is a small share but the hardest to attribute — the thorniest part of gray-zone threats."),
        ("How long does it take to repair a broken cable?",
         "Usually 1 to 1.5 months, sometimes longer. One reason is that only about 60 specialized cable-repair ships exist worldwide and are often queued; plus the break must be located precisely, then grappled up, spliced, and tested — deep water is especially slow. Long repair times are exactly why outer islands served by only a few cables fear a break most."),
    ],
}

# English breadcrumb names. Intermediate ancestor names are translated by the
# generic map; the trailing crumb (the page itself) uses this per-page label.
EN_BREADCRUMB_NAMES = {
    "首頁": "Home",                       # 首頁
    "首頁 Home": "Home",
    "深度文章": "In-Depth Articles",   # 深度文章
    "關於本站 About": "About",        # 關於本站 About
    "研究報告 Research": "Research",   # 研究報告 Research
}
EN_BREADCRUMB_LAST = {
    "blog.html": "In-Depth Articles",
    "blog-methodology.html": "Our Methodology",
    "blog-what-is-ais.html": "What Is AIS?",
    "blog-what-is-submarine-cable.html": "What Is a Submarine Cable?",
    "blog-cable-threats.html": "Threats to Submarine Cables",
    "blog-taiwan-cable-status.html": "Taiwan's Cable Situation",
    "blog-taiwan-enforcement.html": "Taiwan's Enforcement Framework",
    "blog-what-is-dark-vessel.html": "What Is a Dark Vessel?",
    "blog-what-is-ship-to-ship-transfer.html": "What Is an STS Transfer?",
    "blog-what-is-ais-spoofing.html": "What Is AIS Spoofing?",
    "blog-what-is-maritime-gray-zone.html": "What Is Maritime Gray-Zone Activity?",
    "blog-what-is-shadow-fleet.html": "What Is a Shadow Fleet?",
    "blog-what-is-maritime-sanctions.html": "What Are Maritime Sanctions?",
    "blog-taiwan-maritime-zones.html": "Taiwan's Maritime Zones",
    "blog-gray-zone-glossary.html": "Gray-Zone Glossary",
    "blog-ocean-law-maritime-rights.html": "What Are Maritime Rights?",
    "blog-ocean-law-cable-protection.html": "Cables Under International Law",
    "blog-ocean-law-enforcement-jurisdiction.html": "Who Can Board and Enforce?",
    "blog-taiwan-cable-infrastructure.html": "Taiwan's Cable Infrastructure",
    "blog-global-cable-industry.html": "The Global Cable Industry",
    "intro.html": "About",
    "research-submarine-cable-legal.html": "Research",
}

# English translation of the intro page's HowTo (usage walkthrough) JSON-LD.
EN_HOWTO = {
    "name": "How to Use the Taiwan Gray Zone & Submarine Cable Monitor",
    "description": "A complete walkthrough from the live map, layer toggles, and the suspicious-vessel list to track-playback animation and statistical analysis.",
    "steps": [
        ("Open the live monitoring map",
         "Enter from the homepage live map to see real-time vessel positions, dark-vessel detections, and submarine-cable routes in the waters around Taiwan."),
        ("Toggle map layers",
         "Use the layer controls to show or hide vessels, dark vessels, submarine cables, and fishing hotspots, focusing on the information you care about."),
        ("Check the suspicious-vessel list",
         "Review suspicious vessels ranked by threat score in the sidebar or bottom panel; click any vessel to see detailed information and risk indicators."),
        ("Replay the track animation",
         "Open the track-animation page to dynamically replay vessels' tracks over the past few days and observe anomalies such as going dark, loitering, or ship-to-ship transfers."),
        ("Compare statistics and exercise predictions",
         "On the statistics page, view dark-vessel trend charts and military-exercise prediction indicators to read the overall maritime situation."),
    ],
}

# Exact-match English for language-neutral UI chrome that the source pages
# hard-code in Chinese with no English variant and no data-i18n key: related/
# series chips, blog-index filter buttons and card tags, series-dot labels and
# title tooltips, stat callouts with Chinese units, and bilingual incident
# names (kept as the English side). Applied to whole text nodes and to title=""
# attributes on the /en/ mirror only.
EN_TEXT = {
    # section / list labels
    "系列文章 SERIES →": "SERIES →",
    "海洋法專區 SERIES →": "SERIES →",
    "延伸閱讀 RELATED →": "RELATED →",
    "主題群 TOPIC MAP →": "TOPIC MAP →",
    # numbered series chips
    "01 海底電纜": "01 Submarine Cable",
    "03 台灣現況": "03 Taiwan Status",
    "04 威脅": "04 Threats",
    "05 執法": "05 Enforcement",
    "06 方法論": "06 Methodology",
    # related chips
    "影子船隊": "Shadow Fleet",
    "暗船": "Dark Vessel",
    "AIS 欺騙": "AIS Spoofing",
    "海底電纜": "Submarine Cable",
    "AIS 是什麼": "What Is AIS",
    "評分方法論": "Methodology",
    "詞彙表": "Glossary",
    "方法論": "Methodology",
    "旁靠": "STS",
    "暗船即時地圖": "Live Dark-Vessel Map",
    "旁靠偵測": "STS Detection",
    "旁靠即時偵測": "Live STS Detection",
    "海纜威脅": "Cable Threats",
    "電纜威脅": "Cable Threats",
    "旁靠 STS": "STS",
    "暗船是什麼": "What Is a Dark Vessel",
    # blog-index filter buttons
    "全部 / All": "All",
    "AIS / 暗船": "AIS / Dark",
    "灰色地帶": "Gray Zone",
    "執法法律": "Enforcement",
    "海洋法": "Law of the Sea",
    "詞彙": "Glossary",
    "參考": "Reference",
    "基礎知識": "Basics",
    "威脅分析": "Threat Analysis",
    "學術研究": "Research",
    # card tags
    "基礎": "Basics",
    "總覽": "Overview",
    "制裁規避": "Sanctions Evasion",
    "海事制裁": "Maritime Sanctions",
    "AIS欺騙": "AIS Spoofing",
    "偵測": "Detection",
    "台灣海纜": "Taiwan Cables",
    "馬祖": "Matsu",
    "基礎設施": "Infrastructure",
    "威脅": "Threats",
    "執法": "Enforcement",
    "方法": "Method",
    "開源": "Open Source",
    "法律分析": "Legal Analysis",
    "國際法": "International Law",
    "管轄權": "Jurisdiction",
    "海纜產業": "Cable Industry",
    "海域分區": "Maritime Zones",
    "海巡署": "Coast Guard",
    "權宜船": "Flag of Convenience",
    "法律戰": "Lawfare",
    "參考 ⏱": "Reference ⏱",
    # series-dot reading-time subtitles (blog index)
    "基礎知識 · 8 min": "Basics · 8 min",
    "台灣 · 馬祖 · 9 min": "Taiwan · Matsu · 9 min",
    "威脅 · 影子船隊 · 11 min": "Threats · Shadow Fleet · 11 min",
    "執法 · UNCLOS · 12 min": "Enforcement · UNCLOS · 12 min",
    "CSIS · 開源 · 13 min": "CSIS · Open Source · 13 min",
    # stat callouts (Chinese units)
    "50天": "50 days",
    "130萬": "1.3M",
    "$10兆": "$10T",
    # bilingual incident names -> English side
    "馬祖雙纜事件 / Matsu Double-Cut Incident": "Matsu Double-Cut Incident",
    "伊鵬三號事件 / Yi Peng 3 Incident (Baltic Sea)": "Yi Peng 3 Incident (Baltic Sea)",
    "興順 39 號 / Xing Shun 39": "Xing Shun 39",
    "鴻泰 58 號 / Hong Tai 58": "Hong Tai 58",
    # series-dot title tooltips (blog index)
    "01 什麼是海底電纜": "01 What Is a Submarine Cable",
    "02 AIS 是什麼": "02 What Is AIS",
    "03 我國海底電纜現況": "03 Taiwan's Cable Situation",
    "04 海底電纜所受到的威脅": "04 Threats to Submarine Cables",
    "05 我國執法框架與挑戰": "05 Enforcement Framework & Challenges",
    "06 本網站所使用之方法": "06 Our Methodology",
}

# JSON-LD @type values whose `headline`/`description`/`name` describe the page
# itself (and so should be rewritten to English from EN_META).
_ARTICLE_TYPES = {"Article", "TechArticle", "ScholarlyArticle", "BlogPosting",
                  "AboutPage", "CollectionPage", "WebPage", "Report"}

_SKIP_PREFIXES = ("http://", "https://", "//", "#", "mailto:", "tel:",
                  "javascript:", "data:", "../")
_ATTR_RE = re.compile(r'\b(href|src)="([^"]+)"')
_HTML_LINK_RE = re.compile(r'^([^/?#]+\.html)(\?[^#]*)?(#.*)?$')


def _rewrite_url(value: str) -> str:
    """Rewrite a single href/src value for a page living under docs/en/."""
    if value.startswith(_SKIP_PREFIXES):
        return value
    m = _HTML_LINK_RE.match(value)
    if m:
        fname, query, frag = m.group(1), m.group(2) or "", m.group(3) or ""
        if fname in EN_SET:
            return value  # sibling /en/ page — relative link still resolves
        return f"../{fname}{query}{frag}"  # app/other page lives at root
    # any other relative asset (css/, js/, manifest.json, *.png, *.ico, …)
    return f"../{value}"


def _rewrite_attrs(html: str) -> str:
    return _ATTR_RE.sub(lambda m: f'{m.group(1)}="{_rewrite_url(m.group(2))}"', html)


def _esc(s: str) -> str:
    """Escape a value for use inside HTML text / a double-quoted attribute."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _sub_once(html: str, pattern: str, value: str) -> str:
    """Replace the inner content of the first match (groups 1+2 bracket it)."""
    return re.sub(pattern, lambda m: m.group(1) + _esc(value) + m.group(2),
                  html, count=1)


def _rewrite_en_head(html: str, meta: dict) -> str:
    """Translate the <head> metadata of a mirror page to English."""
    html = _sub_once(html, r"(<title>).*?(</title>)", meta["title"])
    html = _sub_once(html, r'(<meta name="description" content=").*?(">)', meta["desc"])
    html = _sub_once(html, r'(<meta name="keywords" content=").*?(">)', meta["kw"])
    html = _sub_once(html, r'(<meta property="og:title" content=").*?(">)', meta["social"])
    html = _sub_once(html, r'(<meta property="og:description" content=").*?(">)', meta["desc"])
    html = _sub_once(html, r'(<meta name="twitter:title" content=").*?(">)', meta["social"])
    html = _sub_once(html, r'(<meta name="twitter:description" content=").*?(">)', meta["desc"])
    # Locale: an /en/ page is en_US, with zh_TW as the alternate.
    html = html.replace('<meta property="og:locale" content="zh_TW">',
                        '<meta property="og:locale" content="en_US">', 1)
    html = html.replace('<meta property="og:locale:alternate" content="en_US">',
                        '<meta property="og:locale:alternate" content="zh_TW">', 1)
    return html


_JSONLD_RE = re.compile(
    r'(<script type="application/ld\+json">\s*)(.*?)(\s*</script>)', re.S)


def _types_of(node: dict) -> set:
    t = node.get("@type")
    if isinstance(t, list):
        return set(t)
    return {t} if t else set()


def _en_url(value: str) -> str:
    """Rewrite a BASE absolute URL to its /en/ mirror when that page exists."""
    if not isinstance(value, str) or not value.startswith(BASE):
        return value
    rest = value[len(BASE):]
    m = re.match(r'^([^/?#]+\.html)', rest)
    if m and m.group(1) in EN_SET:
        return BASE + "en/" + rest
    return value


def _translate_node(node: dict, page: str, has_ai_summary: bool) -> None:
    """Localize a single JSON-LD object to English (recursing into children)."""
    types = _types_of(node)
    page_urls = {BASE + page, BASE + "en/" + page}

    # FAQPage — swap the Chinese Q&A for the English translation.
    if "FAQPage" in types and page in EN_FAQ:
        node["mainEntity"] = [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in EN_FAQ[page]
        ]

    # BreadcrumbList — translate each crumb's display name.
    if "BreadcrumbList" in types:
        for item in node.get("itemListElement", []):
            if item.get("item") in page_urls and page in EN_BREADCRUMB_LAST:
                item["name"] = EN_BREADCRUMB_LAST[page]
            elif item.get("name") in EN_BREADCRUMB_NAMES:
                item["name"] = EN_BREADCRUMB_NAMES[item["name"]]

    # ItemList — the blog index: translate the list title and each entry's
    # name from EN_META (entries link to the mirrored article pages).
    if "ItemList" in types:
        if node.get("url") in page_urls and page in EN_META:
            node["name"] = EN_META[page]["social"]
            node["description"] = EN_META[page]["desc"]
        for item in node.get("itemListElement", []):
            u = item.get("url", "")
            if isinstance(u, str) and u.startswith(BASE):
                fn = u[len(BASE):].split("#")[0].split("?")[0]
                if fn in EN_META:
                    item["name"] = EN_META[fn]["social"]

    # DefinedTermSet / DefinedTerm — make the glossary schema English-only:
    # drop the Chinese alternateName and the Chinese half of bilingual
    # "English / 中文" definitions, matching the English-only DOM.
    if types & {"DefinedTermSet", "DefinedTerm"}:
        node.pop("alternateName", None)
        desc = node.get("description")
        if isinstance(desc, str) and " / " in desc:
            kept = [s for s in desc.split(" / ")
                    if not re.search(r"[一-鿿]", s)]
            if kept:
                node["description"] = " / ".join(kept).strip()

    # HowTo — only the intro usage walkthrough; translate name/desc/steps.
    if "HowTo" in types and page == "intro.html":
        node["name"] = EN_HOWTO["name"]
        node["description"] = EN_HOWTO["description"]
        steps = node.get("step", [])
        for step, (sn, st) in zip(steps, EN_HOWTO["steps"]):
            step["name"] = sn
            step["text"] = st

    # The node describing the page itself — translate headline/description/name
    # from EN_META and attach mainEntityOfPage (+ speakable when present).
    if (types & _ARTICLE_TYPES) and node.get("url") in page_urls and page in EN_META:
        meta = EN_META[page]
        if "headline" in node:
            node["headline"] = meta["social"]
        if "description" in node:
            node["description"] = meta["desc"]
        if "name" in node and "headline" not in node:
            node["name"] = meta["title"]
        node["mainEntityOfPage"] = {
            "@type": "WebPage", "@id": BASE + "en/" + page}
        if has_ai_summary:
            node["speakable"] = {"@type": "SpeakableSpecification",
                                 "cssSelector": [".ai-summary"]}

    # inLanguage everywhere -> en.
    if "inLanguage" in node:
        node["inLanguage"] = "en"

    for value in node.values():
        if isinstance(value, dict):
            _translate_node(value, page, has_ai_summary)
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, dict):
                    _translate_node(v, page, has_ai_summary)


def _rewrite_urls(obj):
    """Recursively rewrite BASE URLs (url/item/@id/…) to their /en/ mirror."""
    if isinstance(obj, dict):
        return {k: _rewrite_urls(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite_urls(v) for v in obj]
    return _en_url(obj)


def _rewrite_en_jsonld(html: str, page: str) -> str:
    """Localize every JSON-LD block on a mirror page to English.

    Parses each ``application/ld+json`` block and rewrites FAQ content,
    breadcrumb names, the page node's headline/description, HowTo steps,
    ``inLanguage``, and any BASE URLs that have an ``/en/`` mirror. Falls back
    to a minimal url/inLanguage regex rewrite if a block is not valid JSON.
    """
    has_ai_summary = 'class="ai-summary"' in html

    def repl(m: "re.Match") -> str:
        try:
            data = json.loads(m.group(2))
        except json.JSONDecodeError:
            block = re.sub(
                r'("(?:url|item)"\s*:\s*")' + re.escape(BASE + page) + r'(")',
                lambda mm: mm.group(1) + BASE + "en/" + page + mm.group(2),
                m.group(2))
            block = re.sub(r'"inLanguage"\s*:\s*(?:\[[^\]]*\]|"[^"]*")',
                           '"inLanguage":"en"', block)
            return m.group(1) + block + m.group(3)
        _translate_node(data, page, has_ai_summary)
        data = _rewrite_urls(data)
        return (m.group(1)
                + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                + m.group(3))

    return _JSONLD_RE.sub(repl, html)


def _load_i18n_en() -> dict:
    """Parse docs/js/i18n.js into a {key: english_string} map.

    The visible nav/header/intro text on the source pages is held in
    ``data-i18n`` spans whose text content is Chinese and swapped to English
    client-side by i18n.js. To emit an English-only DOM we resolve those keys
    to their English value from the same dictionary, so the two never drift.
    """
    try:
        js = (DOCS / "js" / "i18n.js").read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for m in re.finditer(
            r"'([\w.]+)'\s*:\s*\{[^{}]*?\ben\s*:\s*'((?:[^'\\]|\\.)*)'", js):
        val = (m.group(2).replace("\\'", "'").replace('\\"', '"')
               .replace("\\\\", "\\"))
        out[m.group(1)] = val
    return out


EN_UI = _load_i18n_en()

_DATA_I18N_RE = re.compile(
    r'(<(\w+)\b[^>]*\sdata-i18n="([\w.]+)"[^>]*>)(.*?)(</\2>)', re.S)
_PLACEHOLDER_RE = re.compile(
    r'(data-i18n-placeholder="([\w.]+)"[^>]*\bplaceholder=")[^"]*(")')
_LANGTOGGLE_BTN_RE = re.compile(r'<button id="langToggle"[^>]*>.*?</button>', re.S)
# The bilingual visibility rule the source pages carry inline. On an English
# mirror (Chinese DOM removed) it is dangerous: if anything ever clears
# body.lang-en, every .lang-en-only block would hide and the page goes blank.
_LANG_VIS_CSS = ('body.lang-en .lang-zh-only{display:none!important}'
                 'body:not(.lang-en) .lang-en-only{display:none!important}')


def _localize_lang_toggle(html: str, page: str) -> str:
    """Turn the in-page ``i18n.toggle()`` button into a link to the zh page.

    On an English-only mirror, calling ``i18n.toggle()`` would switch the body
    to ``lang-zh`` and hide all (English) content, leaving a blank page — the
    Chinese DOM no longer exists. Replacing it with a plain anchor to the
    Chinese canonical (``../<page>``) makes the switch a real, safe navigation.
    """
    m = _LANGTOGGLE_BTN_RE.search(html)
    if not m:
        return html
    sm = re.search(r'\sstyle="([^"]*)"', m.group(0))
    style = (sm.group(1) + ";" if sm else "") + "text-decoration:none;display:inline-block"
    anchor = (f'<a id="langToggle" href="../{page}" style="{style}" '
              f'title="切換為中文 / Read in Chinese">中</a>')
    return html[:m.start()] + anchor + html[m.end():]


def _resolve_data_i18n(html: str) -> str:
    """Replace data-i18n element text (and placeholders) with English."""
    def text(m):
        en = EN_UI.get(m.group(3))
        return m.group(1) + _esc(en) + m.group(5) if en is not None else m.group(0)

    def ph(m):
        en = EN_UI.get(m.group(2))
        return m.group(1) + _esc(en) + m.group(3) if en is not None else m.group(0)

    html = _DATA_I18N_RE.sub(text, html)
    html = _PLACEHOLDER_RE.sub(ph, html)
    return html


def _strip_zh_only(html: str) -> str:
    """Remove every element carrying the ``lang-zh-only`` class.

    Depth-aware so a Chinese block that nests same-named tags (``div`` in
    ``div``) is removed whole. The parallel ``lang-en-only`` siblings remain
    and render because the mirror's ``<body>`` is ``lang-en``.
    """
    open_re = re.compile(
        r'<(\w+)([^>]*\bclass="[^"]*\blang-zh-only\b[^"]*"[^>]*)>', re.I)
    out, i = [], 0
    while True:
        m = open_re.search(html, i)
        if not m:
            out.append(html[i:])
            break
        out.append(html[i:m.start()])
        tag = m.group(1)
        if m.group(2).rstrip().endswith("/"):   # self-closing — drop just it
            i = m.end()
            continue
        depth, j = 1, m.end()
        tag_re = re.compile(r'<(/?)' + re.escape(tag) + r'\b([^>]*)>', re.I)
        while depth > 0:
            tm = tag_re.search(html, j)
            if not tm:
                j = len(html)
                break
            if tm.group(1) == "/":
                depth -= 1
            elif not tm.group(2).rstrip().endswith("/"):
                depth += 1
            j = tm.end()
        i = j
        k = i                                    # swallow trailing blank line
        while k < len(html) and html[k] in " \t":
            k += 1
        if k < len(html) and html[k] == "\n":
            i = k + 1
    return "".join(out)


_GZH_RE = re.compile(r'\s*<span class="g-zh">.*?</span>')


def _translate_static_text(html: str) -> str:
    """Translate hard-coded Chinese UI labels (whole text nodes + titles)."""
    for zh, en in EN_TEXT.items():
        html = re.sub(r'(>)\s*' + re.escape(zh) + r'\s*(<)',
                      lambda m, en=en: m.group(1) + _esc(en) + m.group(2), html)
        html = html.replace(f'title="{zh}"', f'title="{_esc(en)}"')
    # Glossary term rows show the Chinese term in a g-zh span beside the English
    # g-en span; drop it so the English mirror reads cleanly.
    html = _GZH_RE.sub("", html)
    return html


def generate_page(page: str) -> str:
    html = (DOCS / page).read_text(encoding="utf-8")

    # Force English document + initial paint.
    html = html.replace('<html lang="zh-TW">', '<html lang="en">', 1)
    if "<body class=" in html[:html.find("<body") + 200] and "<body>" not in html:
        html = re.sub(r'<body class="([^"]*)"',
                      lambda m: f'<body class="{m.group(1)} lang-en"', html, count=1)
    else:
        html = html.replace("<body>", '<body class="lang-en">', 1)

    # Canonical + og:url should point at the /en/ URL for this page.
    html = html.replace(f'canonical" href="{BASE}{page}"',
                        f'canonical" href="{BASE}en/{page}"')
    html = html.replace(f'og:url" content="{BASE}{page}"',
                        f'og:url" content="{BASE}en/{page}"')

    # English <head> metadata + JSON-LD url/inLanguage (clean SEO for /en/).
    if page in EN_META:
        html = _rewrite_en_head(html, EN_META[page])
    html = _rewrite_en_jsonld(html, page)

    # English-only DOM: resolve data-i18n text, drop Chinese-only blocks, and
    # flip the language toggle to offer Chinese (so non-JS / text-only crawlers
    # see clean English instead of CSS-hidden bilingual content).
    html = _resolve_data_i18n(html)
    html = _strip_zh_only(html)
    html = _translate_static_text(html)
    # Make English content unconditional and the toggle a safe link, so the
    # mirror can never blank out if the body's lang class changes.
    html = html.replace(_LANG_VIS_CSS, ".lang-zh-only{display:none!important}")
    html = _localize_lang_toggle(html, page)

    # Fix relative asset/link paths for the deeper directory.
    html = _rewrite_attrs(html)

    marker = (f"<!-- AUTO-GENERATED English mirror of /{page} by "
              f"src/generate_i18n_pages.py — do not edit by hand. -->\n")
    html = html.replace("<!DOCTYPE html>", "<!DOCTYPE html>\n" + marker, 1)
    return html


def main() -> None:
    EN_DIR.mkdir(parents=True, exist_ok=True)
    # Clean stale generated pages (only .html; keep nothing else there).
    for old in EN_DIR.glob("*.html"):
        old.unlink()

    written = []
    for page in STATIC_PAGES:
        src = DOCS / page
        if not src.exists():
            print(f"  WARN source missing, skipped: {page}")
            continue
        (EN_DIR / page).write_text(generate_page(page), encoding="utf-8")
        written.append(page)

    print(f"Generated {len(written)} English page(s) into docs/en/:")
    for p in written:
        print(f"  en/{p}")


if __name__ == "__main__":
    main()
