import argparse
import datetime
import json
import os
import sys

import requests

STUDY_TYPES = {
    "Affinity Proteomics": "protein measurements with antibody or aptamer panels such as Olink or SomaScan instead of mass spectrometry; choose it for these datasets even though they are not MS. Not for co-immunoprecipitation, pull-down, affinity purification MS, BioID or chemical proteomics, which are Ms Proteomics",
    "Ms Metabolomics": "the deposited data are mass spectrometry measurements of metabolites, not proteins or peptides",
    "Ms Proteomics": "mass spectrometry based proteomics of proteins or peptides, including co-IP, pull-down, AP-MS, BioID and chemical proteomics",
    "Clinical Metadata": "human clinical samples with patient level metadata",
    "Human": "samples from humans or human tissues or body fluids",
    "Human Gut": "a mixed microbial community from the human gut, such as stool or intestinal microbiome samples",
    "Invertebrates": "samples from invertebrate animals such as insects, nematodes, molluscs or worms (not bacteria, archaea, fungi or protists)",
    "Metaproteomics": "proteins from a mixed community of many different microorganisms analysed together, such as a microbiome, biofilm community or environmental sample. Not a single species or strain grown in pure culture, even if it was isolated from the environment",
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

FIELDS = ["title", "keywords", "projectTags", "experimentTypes", "organisms", "organismParts", "diseases",
          "instruments", "quantificationMethods", "sampleAttributes", "projectDescription",
          "sampleProcessingProtocol", "dataProcessingProtocol"]


def fetch_project(pxd):
    r = requests.get(f"https://www.ebi.ac.uk/pride/ws/archive/v3/projects/{pxd}", timeout=60)
    r.raise_for_status()
    return r.json()


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


def classify(pxd, text, model, api_key):
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
    }
    if not model.startswith(("gpt-5", "o")):
        body["temperature"] = 0
    r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=120,
                      headers={"Authorization": f"Bearer {api_key}"})
    if not r.ok:
        raise RuntimeError(f"OpenAI API {r.status_code}: {r.text[:300]}")
    return json.loads(r.json()["choices"][0]["message"]["content"])


def check_study(pxd, model, max_chars=3000):
    result = {"pxd": pxd, "model": model, "date": datetime.datetime.now().isoformat(timespec="seconds"),
              "title": "", "study_types": [], "excluded_types": []}
    try:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        try:
            project = fetch_project(pxd)
        except requests.HTTPError as e:
            raise RuntimeError(f"no PRIDE metadata for {pxd} ({e.response.status_code})")
        result["title"] = project.get("title") or ""
        if not any(project.get(f) for f in ("title", "projectDescription", "sampleProcessingProtocol", "keywords")):
            raise RuntimeError(f"PRIDE metadata for {pxd} has no title, description, protocol or keywords")
        answer = classify(pxd, describe(project, max_chars), model, api_key)
        excluded = [t for t in answer["study_types"] if t in EXCLUDED]
        result.update(study_types=answer["study_types"], excluded_types=excluded, reason=answer["reason"],
                      decision="skip" if excluded else "run" if answer["study_types"] else "unknown")
    except Exception as e:
        result.update(reason=f"check failed: {e}", decision="unknown")
    return result


def main():
    p = argparse.ArgumentParser(description="Decide from the PRIDE metadata whether a PXD is a study type this pipeline covers")
    p.add_argument("--PXD", required=True)
    p.add_argument("--output", default=None)
    p.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))
    p.add_argument("--max_chars", type=int, default=3000)
    p.add_argument("--if_unknown", choices=["run", "skip"], default=os.environ.get("STUDY_CHECK_IF_UNKNOWN", "run"))
    a = p.parse_args()

    result = check_study(a.PXD, a.model, a.max_chars)
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
