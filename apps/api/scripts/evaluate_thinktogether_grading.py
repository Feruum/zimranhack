"""Calibrate the actual configured ThinkTogether reasoning grader against experts.

Usage (from apps/api, with its environment and provider credentials):
  python scripts/evaluate_thinktogether_grading.py --input expert.jsonl --check-input
  python scripts/evaluate_thinktogether_grading.py --input expert.jsonl --output report.json

Supply >=100 genuine, independently expert-labeled reasoning examples. Each JSONL
row has id, prompt, rubric {version, dimensions}, response {text}, and expert
{score, dimensions, reviewer_id, rationale}. Scores are criterion scores in [0,1].
This tool never creates labels, infers cognitive norms, or authorizes a release.
Reports omit candidate text and expert identities; secure the input separately.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def finite_score(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def load_examples(path):
    examples, identifiers = [], set()
    with Path(path).open(encoding='utf-8') as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError()
                identifier = row['id']
                if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
                    raise ValueError('Example IDs must be nonempty and unique')
                if not isinstance(row['prompt'], str) or not 1 <= len(row['prompt'].strip()) <= 30000:
                    raise ValueError('Scenario text is required')
                rubric, response, expert = row['rubric'], row['response'], row['expert']
                if not isinstance(rubric.get('version'), str) or not rubric['version'].strip():
                    raise ValueError('A versioned expert rubric is required')
                dimensions = rubric.get('dimensions')
                if not isinstance(dimensions, list) or not dimensions:
                    raise ValueError('Rubric dimensions are required')
                names = [d.get('id', d.get('name', '')) if isinstance(d, dict) else d for d in dimensions]
                if any(not isinstance(n, str) or not n.strip() for n in names) or len(set(names)) != len(names):
                    raise ValueError('Rubric dimension IDs must be unique')
                if not isinstance(response.get('text'), str) or not 1 <= len(response['text'].strip()) <= 20000:
                    raise ValueError('A candidate reasoning response is required')
                if not finite_score(expert.get('score')) or not isinstance(expert.get('dimensions'), dict) or set(expert['dimensions']) != set(names) or any(not finite_score(n) for n in expert['dimensions'].values()):
                    raise ValueError('Expert overall and dimension criterion scores must be finite and complete')
                if any(not isinstance(expert.get(k), str) or not expert[k].strip() for k in ('reviewer_id', 'rationale')):
                    raise ValueError('Expert reviewer provenance and rationale are required')
            except (KeyError, TypeError, AttributeError, json.JSONDecodeError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else 'Malformed expert-labeled example'
                # Do not print the input row, which can contain private learner text.
                raise ValueError(f'Line {line_number}: {reason or "Malformed expert-labeled example"}') from None
            identifiers.add(identifier)
            examples.append(row)
    if len(examples) < 100:
        raise ValueError('At least 100 unique genuine expert-labeled reasoning examples are required')
    return examples


def summarize(results, *, model, dataset_sha256, threshold, tolerance):
    scored = [r for r in results if r.get('source') == 'ai' and finite_score(r.get('model_score'))]
    confusion = dict(true_positive=0, true_negative=0, false_positive=0, false_negative=0)
    disagreements, errors, dimension_errors = [], [], []
    for row in results:
        if row not in scored:
            disagreements.append({'id': row['id'], 'status': 'provider_grade_unavailable'})
            continue
        expert, predicted = row['expert_score'], row['model_score']
        actual_positive, model_positive = expert >= threshold, predicted >= threshold
        cell = ('true_positive' if actual_positive else 'false_positive') if model_positive else ('false_negative' if actual_positive else 'true_negative')
        confusion[cell] += 1
        error = abs(expert-predicted)
        errors.append(error)
        deltas = {name: abs(value-row.get('model_dimensions', {}).get(name, float('inf'))) for name, value in row.get('expert_dimensions', {}).items()}
        dimension_errors.extend(deltas.values())
        if error > tolerance or actual_positive != model_positive or any(d > tolerance for d in deltas.values()):
            disagreements.append({'id': row['id'], 'expert_score': expert, 'model_score': predicted,
                'absolute_error': error, 'criterion_decision_agrees': actual_positive == model_positive,
                'dimension_absolute_errors': {k: v if math.isfinite(v) else None for k, v in deltas.items()}})
    count = len(scored)
    total = sum(confusion.values())
    observed = (confusion['true_positive']+confusion['true_negative'])/total if total else None
    actual_positive_count = confusion['true_positive']+confusion['false_negative']
    predicted_positive_count = confusion['true_positive']+confusion['false_positive']
    chance = ((actual_positive_count*predicted_positive_count)+(total-actual_positive_count)*(total-predicted_positive_count))/(total*total) if total else None
    kappa = (observed-chance)/(1-chance) if chance is not None and chance < 1 else None
    return {'report_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'model': model,
        'dataset_sha256': dataset_sha256, 'examples': len(results), 'scored': count,
        'unavailable': len(results)-count, 'evaluation_complete': count == len(results),
        'criterion_threshold': threshold, 'score_tolerance': tolerance,
        'agreement': {'exact_score': sum(e == 0 for e in errors)/count if count else None,
            'within_tolerance': sum(e <= tolerance for e in errors)/count if count else None,
            'mean_absolute_error': sum(errors)/count if count else None,
            'root_mean_squared_error': math.sqrt(sum(e*e for e in errors)/count) if count else None,
            'criterion_decision': observed, 'cohens_kappa': kappa,
            'dimension_mean_absolute_error': sum(dimension_errors)/len(dimension_errors) if dimension_errors and all(math.isfinite(d) for d in dimension_errors) else None},
        'confusion': confusion, 'disagreements': disagreements,
        'interpretation': 'Calibration evidence for expert review only. This report does not approve a model or release; label authenticity and representativeness remain external requirements.'}


async def evaluate(examples, *, concurrency, org_id):
    # Import only after offline validation; --help/--check-input need no provider.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.services.thinktogether.learning_evidence import score_response
    semaphore = asyncio.Semaphore(concurrency)
    async def grade(example):
        item = SimpleNamespace(kind='reasoning', prompt=example['prompt'], rubric=example['rubric'])
        async with semaphore:
            score, source, feedback, dimensions, model = await score_response(item, example['response'], allow_ai=True,
                org_id=org_id, usage_purpose='reasoning.evaluation')
        return {'id': example['id'], 'expert_score': example['expert']['score'], 'expert_dimensions': example['expert']['dimensions'],
                'model_score': score, 'model_dimensions': dimensions, 'source': source}
    results = await asyncio.gather(*(grade(example) for example in examples))
    return results, hashlib.sha256(inspect.getsource(score_response).encode()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', required=True, type=Path, help='JSONL with at least 100 genuine expert labels')
    parser.add_argument('--output', type=Path, help='Destination report JSON; required for provider evaluation')
    parser.add_argument('--check-input', action='store_true', help='Validate expert labels offline; do not invoke a provider')
    parser.add_argument('--org-id', type=int, help='Existing organization ID for content-free provider usage accounting')
    parser.add_argument('--concurrency', type=int, default=2, choices=range(1, 9), metavar='1..8')
    parser.add_argument('--threshold', type=float, default=.8, help='Criterion decision threshold; not a cognitive norm')
    parser.add_argument('--tolerance', type=float, default=.1, help='Allowed absolute criterion score difference')
    args = parser.parse_args(argv)
    if not finite_score(args.threshold) or not finite_score(args.tolerance):
        parser.error('Threshold and tolerance must be finite values from zero to one')
    try:
        examples = load_examples(args.input)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
    rubrics = dict(Counter(e['rubric']['version'] for e in examples))
    if args.check_input:
        report = {'check': 'input_valid', 'examples': len(examples), 'dataset_sha256': digest, 'rubric_versions': rubrics,
                  'provider_evaluation': 'not_run', 'notice': 'Expert label authenticity is an external requirement; no calibration or release approval is implied.'}
    else:
        if not args.output:
            parser.error('--output is required for provider evaluation')
        if args.org_id is None or args.org_id <= 0:
            parser.error('--org-id must identify an existing organization for provider usage accounting')
        # Match application configuration loading before resolving the selected model.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from config.config import get_learnhouse_config
        get_learnhouse_config()
        model = os.getenv('TT_REASONING_MODEL', '').strip()
        if not model:
            parser.error('Configure TT_REASONING_MODEL and real provider credentials before evaluation')
        results, grader_digest = asyncio.run(evaluate(examples, concurrency=args.concurrency, org_id=args.org_id))
        report = summarize(results, model=model, dataset_sha256=digest, threshold=args.threshold, tolerance=args.tolerance)
        report.update(grader_sha256=grader_digest, rubric_versions=rubrics)
    serialized = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized+'\n', encoding='utf-8')
        print(json.dumps({'output': str(args.output), 'examples': len(examples), 'provider_evaluation': report.get('provider_evaluation', 'run')}))
    else:
        print(serialized)
    return 0 if args.check_input or report['evaluation_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
