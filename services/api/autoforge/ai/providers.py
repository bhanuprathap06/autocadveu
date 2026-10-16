"""AI provider abstraction.

* ``HeuristicProvider`` - deterministic, local, no data leaves the server.
  Rule-based extraction; every output is labelled with its classification.
* ``AnthropicProvider`` - Claude via the official SDK with schema-validated
  structured outputs (``messages.parse`` + Pydantic). Refusals are surfaced,
  never papered over. Used only when configured AND the caller explicitly
  allowed sending the content to an external provider for that request.

Neither provider can execute code, SQL, shell or solver commands: outputs are
data validated against the schemas in ``schemas.py`` and then routed through
the command registry / CAD kernel by the API layer.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from ..catalog.templates import DOMAINS, TEMPLATES
from ..core.config import get_settings
from .schemas import CadProposal, Claim, Concept, ConceptSet, ExtractedRequirement, Extraction, ParameterSetting


class AIError(RuntimeError):
    def __init__(self, message: str, code: str = "ai_failed"):
        super().__init__(message)
        self.code = code


class Provider(Protocol):
    name: str
    external: bool

    def extract(self, statement: str, domain: str | None) -> Extraction: ...

    def concepts(self, statement: str, requirements: list[dict[str, Any]], domain: str | None) -> ConceptSet: ...

    def propose(self, statement: str, requirements: list[dict[str, Any]], template: str,
                template_params: list[dict[str, Any]], materials: list[str]) -> CadProposal: ...


# --------------------------------------------------------------------------- heuristic
UNIT_METRIC = {
    "kg": "mass", "g": "mass", "t": "mass", "lb": "mass",
    "mm": "length", "cm": "length", "m": "length", "in": "length",
    "n": "force", "kn": "force", "nm": "torque", "n·m": "torque", "n*m": "torque",
    "mpa": "stress", "gpa": "stress", "kpa": "pressure", "bar": "pressure",
    "kw": "power", "w": "power", "hp": "power", "rpm": "speed", "km/h": "vehicle_speed",
    "°c": "temperature", "degc": "temperature", "c": "temperature",
    "v": "voltage", "ah": "capacity", "kwh": "energy", "hz": "frequency", "%": "ratio", "s": "time", "h": "time",
}
NUM_UNIT = re.compile(r"(?P<cmp>less than|under|below|at most|no more than|not exceed(?:ing)?|maximum(?: of)?|max\.?|up to|"
                      r"<=|≤|<|at least|minimum(?: of)?|min\.?|no less than|more than|above|over|exceed(?:ing)?|>=|≥|>)?"
                      r"\s*(?P<num>-?\d+(?:[.,]\d+)?)\s*(?P<unit>kWh|km/h|°C|degC|N·m|N\*m|Nm|kN|MPa|GPa|kPa|bar|kW|hp|rpm|Ah|Hz|"
                      r"kg|mm|cm|lb|W|V|N|g|m|s|h|%)(?![A-Za-z])", re.IGNORECASE)
LE = {"less than", "under", "below", "at most", "no more than", "not exceed", "not exceeding", "maximum", "maximum of", "max",
      "max.", "up to", "<=", "≤", "<"}
GE = {"at least", "minimum", "minimum of", "min", "min.", "no less than", "more than", "above", "over", "exceed", "exceeding",
      ">=", "≥", ">"}
OBLIGATION = re.compile(r"\b(must|shall|required|requires|needs? to|has to|should|is to)\b", re.IGNORECASE)
ASSUMPTION = re.compile(r"\b(assume|assuming|assumption|presume|we expect|expected to be)\b", re.IGNORECASE)
QUESTION = re.compile(r"(\?|\b(unknown|tbd|to be determined|unclear|not sure)\b)", re.IGNORECASE)
KEYWORD_TEMPLATES = {
    "bracket": "l_bracket", "mount": "l_bracket", "brake": "brake_rotor", "rotor": "brake_rotor", "disc": "brake_rotor",
    "shaft": "stepped_shaft", "axle": "stepped_shaft", "coupling": "flange_coupling", "flange": "flange_coupling",
    "battery": "battery_enclosure", "enclosure": "battery_enclosure", "module": "battery_enclosure",
    "sensor": "sensor_housing", "housing": "sensor_housing", "control arm": "control_arm_plate", "wishbone": "control_arm_plate",
    "suspension": "control_arm_plate", "cooling plate": "cooling_plate", "coolant": "cooling_plate", "thermal": "cooling_plate",
}
CHECKLIST = [
    ("mass", "What is the maximum allowable mass?"),
    ("force", "What loads act on the component (magnitude, direction, static or cyclic)?"),
    ("temperature", "What is the operating temperature range?"),
    ("quantity", "What production volume and manufacturing process are intended?"),
    ("standard", "Which regulations or standards apply (e.g. ECE, FMVSS, ISO 26262, ISO 6469)?"),
    ("safety_factor", "What safety factor or reliability target should the design meet?"),
    ("environment", "What environmental exposure is expected (corrosion, water, dust, vibration)?"),
]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+|\s+-\s+", text.strip())
    return [p.strip(" -*\t") for p in parts if len(p.strip(" -*\t")) > 3]


class HeuristicProvider:
    name = "heuristic-rules-v1"
    external = False

    def extract(self, statement: str, domain: str | None) -> Extraction:
        reqs: list[ExtractedRequirement] = []
        seen_metrics: set[str] = set()
        for s in _sentences(statement):
            if QUESTION.search(s):
                reqs.append(ExtractedRequirement(kind="open_question", text=s, claim_class="source_supported_fact",
                                                 rationale="Statement contains a question or an explicitly unknown value"))
                continue
            if ASSUMPTION.search(s):
                reqs.append(ExtractedRequirement(kind="assumption", text=s, claim_class="source_supported_fact",
                                                 rationale="Statement phrased as an assumption"))
                continue
            matches = list(NUM_UNIT.finditer(s))
            if matches:
                for m in matches:
                    cmp_word = (m.group("cmp") or "").lower().strip()
                    comparator = "<=" if cmp_word in LE else ">=" if cmp_word in GE else "="
                    unit = m.group("unit")
                    metric = UNIT_METRIC.get(unit.lower(), "value")
                    seen_metrics.add(metric)
                    kind = "target_metric" if (OBLIGATION.search(s) or cmp_word) else "constraint"
                    reqs.append(ExtractedRequirement(
                        kind=kind, text=s, metric=metric, comparator=comparator,
                        target_value=float(m.group("num").replace(",", ".")), unit=unit, claim_class="source_supported_fact",
                        rationale=f"Numeric value with unit '{unit}' and comparator '{cmp_word or 'none'}' found in the statement"))
            elif OBLIGATION.search(s):
                reqs.append(ExtractedRequirement(kind="functional", text=s, claim_class="source_supported_fact",
                                                 rationale="Obligation wording without a numeric target"))
        questions = []
        low = statement.lower()
        for key, q in CHECKLIST:
            if key not in seen_metrics and key not in low:
                questions.append(q)
        guess = domain or self._domain(low)
        return Extraction(summary=f"Rule-based extraction found {len(reqs)} candidate requirement(s). Review each item: the "
                                  "heuristic does not understand context and may misclassify statements.",
                          domain_guess=guess, requirements=reqs, clarifying_questions=questions[:6])

    @staticmethod
    def _domain(low: str) -> str | None:
        hints = {"braking_steering": ["brake", "caliper", "steering", "rotor"], "ev_battery": ["battery", "cell", "pack", "ev "],
                 "chassis_suspension": ["suspension", "control arm", "subframe", "damper", "spring"],
                 "powertrain": ["gear", "shaft", "transmission", "differential", "crank"],
                 "combustion_cooling": ["radiator", "coolant", "piston", "cylinder head"],
                 "aero_body": ["drag", "spoiler", "diffuser", "aerodynamic"], "electronics": ["sensor", "ecu", "connector", "wiring"],
                 "mechanical": ["bracket", "bearing", "fastener", "bolt", "housing"]}
        scores = {d: sum(low.count(k) for k in ks) for d, ks in hints.items()}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else None

    def concepts(self, statement: str, requirements: list[dict[str, Any]], domain: str | None) -> ConceptSet:
        low = statement.lower()
        keys: list[str] = []
        for kw, tpl in KEYWORD_TEMPLATES.items():
            if kw in low and tpl not in keys:
                keys.append(tpl)
        if not keys and domain:
            keys = [k for k, t in TEMPLATES.items() if t["domain"] == domain][:2]
        concepts = []
        for k in keys[:3]:
            t = TEMPLATES[k]
            claims = [Claim(text=f"The '{t['name']}' starter template matches keywords in the problem statement.",
                            claim_class="unverified_suggestion"),
                      Claim(text="Template proportions are illustrative and have not been sized against the stated requirements.",
                            claim_class="source_supported_fact", reference=f"template:{k}")]
            metric_reqs = [r for r in requirements if r.get("kind") == "target_metric"]
            for r in metric_reqs[:3]:
                claims.append(Claim(text=f"Verify the concept against: {r['text']}", claim_class="design_recommendation",
                                    reference=f"requirement:{r.get('id')}"))
            concepts.append(Concept(title=t["name"], description=t["description"], template=k,
                                    catalog_queries=[t["category"].replace("_", " ")], claims=claims,
                                    assumptions=["Default template material and dimensions until edited"]))
        if not concepts:
            concepts.append(Concept(title="Start from a blank part", description="No starter template matched the statement.",
                                    claims=[Claim(text="No keyword match; model from scratch in the CAD workbench.",
                                                  claim_class="unverified_suggestion")]))
        return ConceptSet(concepts=concepts)

    def propose(self, statement: str, requirements: list[dict[str, Any]], template: str,
                template_params: list[dict[str, Any]], materials: list[str]) -> CadProposal:
        # Deterministic: keep template defaults; map explicit length targets onto same-named parameters only.
        params: list[ParameterSetting] = []
        names = {p["name"].lower(): p["name"] for p in template_params}
        for r in requirements:
            if r.get("kind") in ("target_metric", "constraint") and r.get("unit") == "mm" and r.get("comparator") == "=":
                for word in re.findall(r"[A-Za-z_]+", r.get("text", "")):
                    if word.lower() in names:
                        params.append(ParameterSetting(name=names[word.lower()], expression=str(r["target_value"]),
                                                       rationale=f"Matched requirement text: {r['text']}"))
        return CadProposal(template=template, name=TEMPLATES[template]["name"], parameters=params, material_key=None,
                           claims=[Claim(text="Proposal keeps template defaults except explicitly named dimensions.",
                                         claim_class="unverified_suggestion")])


# --------------------------------------------------------------------------- Anthropic
SYSTEM = (
    "You are an automotive engineering assistant inside AutoForge Engineering Studio. You help engineers turn problem "
    "statements into reviewable requirements and concepts. Rules: (1) Never invent manufacturer specifications, test results, "
    "certifications or standards clauses; if something is unknown, say so and ask. (2) Classify every claim as exactly one of "
    "source_supported_fact (directly stated in the user's text or the provided data), calculation (derived arithmetically "
    "from provided values; state the inputs), engineering_hypothesis, design_recommendation, or unverified_suggestion. "
    "(3) Do not claim a design is optimised, manufacturable or safe. (4) Prefer asking clarifying questions over guessing "
    "loads, materials or regulations. (5) Only reference templates, parameters and materials from the lists you are given."
)


class AnthropicProvider:
    external = True

    def __init__(self) -> None:
        s = get_settings()
        if not s.anthropic_api_key:
            raise AIError("Anthropic provider selected but no API key configured", "ai_not_configured")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=120.0, max_retries=2)
        self.model = s.ai_model
        self.name = f"anthropic:{self.model}"

    def _parse(self, prompt: str, schema: type) -> Any:
        a = self._anthropic
        try:
            kwargs: dict[str, Any] = {}
            if self.model in ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"):
                # Server-side refusal fallback (routes declined requests by category).
                kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
            resp = self.client.beta.messages.parse(
                model=self.model, max_tokens=16000, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}], output_format=schema, **kwargs)
        except a.RateLimitError as exc:
            raise AIError("AI provider rate limit reached; try again shortly", "ai_rate_limited") from exc
        except a.APIStatusError as exc:
            raise AIError(f"AI provider error ({exc.status_code})", "ai_provider_error") from exc
        except a.APIConnectionError as exc:
            raise AIError("Could not reach the AI provider", "ai_unreachable") from exc
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise AIError(f"The AI provider declined this request (category: {cat or 'unspecified'})", "ai_refused")
        if resp.stop_reason == "max_tokens":
            raise AIError("AI response was truncated; shorten the problem statement", "ai_truncated")
        out = resp.parsed_output
        if out is None:
            raise AIError("AI response did not match the expected schema", "ai_invalid_output")
        return out

    def extract(self, statement: str, domain: str | None) -> Extraction:
        return self._parse(
            f"Domain hint: {domain or 'unknown'}. Known domains: {sorted(DOMAINS)}.\n"
            "Extract functional requirements, constraints, numeric target metrics (metric, comparator, value, unit), assumptions "
            "and open questions from the problem statement below. Quote or closely paraphrase the user's text; do not add "
            "requirements the user did not state (put those in clarifying_questions instead).\n\n"
            f"<problem_statement>\n{statement}\n</problem_statement>", Extraction)

    def concepts(self, statement: str, requirements: list[dict[str, Any]], domain: str | None) -> ConceptSet:
        tpl = [{"key": k, "name": v["name"], "domain": v["domain"], "description": v["description"]} for k, v in TEMPLATES.items()]
        return self._parse(
            f"Approved requirements (JSON): {requirements}\nAvailable starter templates: {tpl}\n"
            "Propose up to 3 candidate concepts. For each, explain trade-offs as classified claims, list assumptions, and give "
            "short catalog search queries. Reference requirement ids as 'requirement:<id>' where relevant.\n\n"
            f"<problem_statement>\n{statement}\n</problem_statement>", ConceptSet)

    def propose(self, statement: str, requirements: list[dict[str, Any]], template: str,
                template_params: list[dict[str, Any]], materials: list[str]) -> CadProposal:
        return self._parse(
            f"Template: {template}. Its parameters (name, current expression, description): {template_params}.\n"
            f"Material keys available: {materials}.\nApproved requirements: {requirements}\n"
            "Propose parameter values for this template that address the requirements. Only use parameter names from the list "
            "and plain numeric expressions in millimetres/degrees. Explain each choice in its rationale and classify claims. "
            "If a requirement cannot be addressed by these parameters, say so in a claim.\n\n"
            f"<problem_statement>\n{statement}\n</problem_statement>", CadProposal)


def get_provider(allow_external: bool) -> Provider:
    s = get_settings()
    if allow_external and s.ai_provider == "anthropic" and s.anthropic_api_key:
        return AnthropicProvider()
    return HeuristicProvider()


def provider_status() -> dict[str, Any]:
    s = get_settings()
    return {"configured_provider": s.ai_provider, "external_available": s.ai_provider == "anthropic" and bool(s.anthropic_api_key),
            "model": s.ai_model if s.ai_provider == "anthropic" else None, "fallback": HeuristicProvider.name,
            "data_policy": "Problem statements and requirements are sent to an external provider only when the user enables it "
                           "for that request. CAD files are never sent."}
