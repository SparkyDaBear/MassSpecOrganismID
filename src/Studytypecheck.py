import argparse
import datetime
import json
import os
import sys
import time

import requests

STUDY_TYPES = {
    "Affinity Proteomics": "the deposited data are protein measurements with antibody or aptamer panels such as Olink or SomaScan instead of mass spectrometry; choose it for these datasets even though they are not MS. If the deposited data are mass spectrometry and Olink or SomaScan is only mentioned, for example as a comparison, do not choose it. Not for co-immunoprecipitation, pull-down, affinity purification MS, BioID or chemical proteomics, which are Ms Proteomics",
    "Ms Metabolomics": "the deposited data are mass spectrometry measurements of metabolites, not proteins or peptides",
    "Ms Proteomics": "mass spectrometry based proteomics of proteins or peptides, including co-IP, pull-down, AP-MS, BioID and chemical proteomics",
    "Clinical Metadata": "human clinical samples with patient level metadata",
    "Human": "samples from humans or human tissues or body fluids",
    "Human Gut": "a mixed microbial community from the human gut, such as stool or intestinal microbiome samples",
    "Invertebrates": "samples from invertebrate animals such as insects, nematodes, molluscs or worms (not bacteria, archaea, fungi or protists)",
    "Metaproteomics": "proteins from a mixed community of many different microorganisms analysed together, such as a microbiome, biofilm community or environmental sample. Not a single species or strain grown in pure culture, even if it was isolated from the environment. Not a human or animal tissue, cell or body fluid sample such as plasma, bile, urine or saliva, even if microbial proteins are also searched or mentioned",
    "Oncology Metadata": "cancer or tumour samples",
    "Plants": "samples from plants or algae",
    "Soil": "a soil, sediment or rhizosphere sample analysed as a whole, not a single organism isolated from soil",
    "Vertebrates": "samples from non-human vertebrates such as mouse, rat, fish, birds or livestock",
    "Water": "a seawater, freshwater, wastewater or sludge sample analysed as a whole, not a single organism isolated from water or an aquatic animal",
    "Cell Lines": "cultured cell lines",
    "Crosslinking": "crosslinking mass spectrometry (XL-MS): a chemical crosslinker such as DSS, BS3, DSSO or DSBU is added to link amino acids and the crosslinked peptide pairs are identified. Not natural crosslinks inside a protein, and not photoaffinity or click chemistry probes for target identification",
    "Dia Acquisition": "data independent acquisition (DIA, SWATH)",
    "Gc Ms Metabolomics": "the deposited data are gas chromatography mass spectrometry measurements of metabolites",
    "Immunopeptidomics": "HLA or MHC bound peptides",
    "Lc Ms Metabolomics": "the deposited data are liquid chromatography mass spectrometry measurements of metabolites or lipids. If the study also did metabolomics or lipidomics but the deposited PRIDE dataset is proteomics, do not choose it",
    "Single Cell": "single cell proteomics",
}

EXCLUDED = [
    "Affinity Proteomics",
    "Ms Metabolomics",
    "Human Gut",
    "Metaproteomics",
    "Soil",
    "Water",
    "Crosslinking",
    "Gc Ms Metabolomics",
    "Lc Ms Metabolomics",
]

PRIDE_TERMS = {
    "Crosslinking": ["crosslinking", "crosslink", "xlms", "clms", "crosslinkingmassspectrometry",
                     "chemicalcrosslinkingcoupledwithmassspectrometryproteomics"],
    "Metaproteomics": ["metaproteomics", "metaproteome"],
    "Soil": ["soil"],
    "Water": ["water", "seawater", "wastewater", "freshwater"],
    "Human Gut": ["humangut", "humangutmicrobiome", "humangutmicrobiota"],
    "Affinity Proteomics": ["affinityproteomics", "olink", "somascan", "olinkaffinityproteomics",
                            "somascanaffinityproteomics"],
}

FIELDS = ["title", "keywords", "projectTags", "experimentTypes", "organisms", "organismParts", "diseases",
          "instruments", "quantificationMethods", "sampleAttributes", "projectDescription",
          "sampleProcessingProtocol", "dataProcessingProtocol"]


def fetch_project(pxd, attempts=3):
    for i in range(attempts):
        try:
            r = requests.get(f"https://www.ebi.ac.uk/pride/ws/archive/v3/projects/{pxd}", timeout=60)
            if r.status_code < 500 or i == attempts - 1:
                r.raise_for_status()
                return r.json()
        except (requests.ConnectionError, requests.Timeout):
            if i == attempts - 1:
                raise
        time.sleep(10 * (i + 1))


def pride_terms(project):
    terms = list(project.get("keywords") or [])
    terms += [x.get("name", "") for x in project.get("experimentTypes") or [] if isinstance(x, dict)]
    found = {}
    for term in terms:
        norm = "".join(c for c in str(term).lower() if c.isalnum())
        for study_type, names in PRIDE_TERMS.items():
            if norm in names:
                found.setdefault(study_type, term)
    return found


def describe(project, max_chars):
    lines = []
    for f in FIELDS:
        v = project.get(f)
        if not v:
            continue
        if isinstance(v, list):
            v = "; ".join(x.get("name", json.dumps(x)) if isinstance(x, dict) else str(x) for x in v)
        lines.append(f"{f}: {str(v)[:max_chars]}")
    return "\n".join(lines)


def classify(pxd, text, model, api_key, temperature):
    system = ("You classify PRIDE mass spectrometry datasets. Choose every study type from the list that "
              "describes the deposited dataset, using only the metadata given. If the metadata is not enough "
              "to decide, return an empty list and say why in the reason. Do not guess. Study types:\n"
              + "\n".join(f"- {k}: {v}" for k, v in STUDY_TYPES.items()))
    schema = {
        "type": "object",
        "properties": {
            "study_types": {"type": "array", "items": {"type": "string", "enum": list(STUDY_TYPES)}},
            "reason": {"type": "string"},
        },
        "required": ["study_types", "reason"],
        "additionalProperties": False,
    }
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": f"Dataset {pxd}\n{text}"}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "study_types", "strict": True, "schema": schema}},
        "provider": {"require_parameters": True},
    }
    if temperature != "default":
        body["temperature"] = float(temperature)
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", json=body, timeout=180,
                      headers={"Authorization": f"Bearer {api_key}"})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or "choices" not in data:
        raise RuntimeError(f"OpenRouter API {r.status_code}: {r.text[:300]}")
    content = (data["choices"][0]["message"].get("content") or "").strip()
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    answer = json.loads(content)
    answer["study_types"] = [t for t in answer.get("study_types", []) if t in STUDY_TYPES]
    answer["reason"] = answer.get("reason", "")
    return answer


def check_study(pxd, model, max_chars=3000, temperature=os.environ.get("STUDY_CHECK_TEMPERATURE", "0")):
    result = {"pxd": pxd, "model": model, "temperature": temperature, "date": datetime.datetime.now().isoformat(timespec="seconds"),
              "title": "", "study_types": [], "excluded_types": []}
    try:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        try:
            project = fetch_project(pxd)
        except requests.HTTPError as e:
            raise RuntimeError(f"no PRIDE metadata for {pxd} ({e.response.status_code})")
        result["title"] = project.get("title") or ""
        if not any(project.get(f) for f in ("title", "projectDescription", "sampleProcessingProtocol", "keywords")):
            raise RuntimeError(f"PRIDE metadata for {pxd} has no title, description, protocol or keywords")
        answer = classify(pxd, describe(project, max_chars), model, api_key, temperature)
        found = pride_terms(project)
        types = answer["study_types"] + [t for t in found if t not in answer["study_types"]]
        excluded = [t for t in types if t in EXCLUDED]
        reason = answer["reason"]
        if found:
            reason += " PRIDE keywords/experiment types: " + "; ".join(f"{t} ({v})" for t, v in found.items()) + "."
        result.update(study_types=types, excluded_types=excluded, pride_terms=found, reason=reason,
                      decision="skip" if excluded else "run" if types else "unknown")
    except Exception as e:
        result.update(reason=f"check failed: {e}", decision="unknown")
    return result


def main():
    p = argparse.ArgumentParser(description="Decide from the PRIDE metadata whether a PXD is a study type this pipeline covers")
    p.add_argument("--PXD", required=True)
    p.add_argument("--output", default=None)
    p.add_argument("--model", default=os.environ.get("STUDY_CHECK_MODEL", "google/gemma-4-31b-it"))
    p.add_argument("--temperature", default=os.environ.get("STUDY_CHECK_TEMPERATURE", "0"))
    p.add_argument("--max_chars", type=int, default=3000)
    p.add_argument("--if_unknown", choices=["run", "skip"], default=os.environ.get("STUDY_CHECK_IF_UNKNOWN", "run"))
    a = p.parse_args()

    result = check_study(a.PXD, a.model, a.max_chars, a.temperature)
    result["if_unknown"] = a.if_unknown

    if a.output:
        os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
        with open(a.output, "w") as fh:
            json.dump(result, fh, indent=2)

    print(f"{a.PXD} study types: {', '.join(result['study_types']) or '-'}")
    print(f"reason: {result['reason']}")
    if result["decision"] == "skip":
        print(f"{a.PXD}: this type of study ({', '.join(result['excluded_types'])}) is not covered by this pipeline; skipping")
        sys.exit(3)
    if result["decision"] == "unknown" and a.if_unknown == "skip":
        print(f"{a.PXD}: study type could not be determined; skipping (STUDY_CHECK_IF_UNKNOWN=skip)")
        sys.exit(3)
    if result["decision"] == "unknown":
        print(f"WARNING {a.PXD}: study type could not be determined; running the pipeline anyway")
    else:
        print(f"{a.PXD}: study type is covered by this pipeline; running")


if __name__ == "__main__":
    main()
