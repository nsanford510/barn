-- Ad-hoc writers stored short verdicts ('vacant', 'occupied') that the
-- properties-list filter never matched. Normalize any stragglers and reject
-- anything outside the canonical vocabulary going forward.

update bills set ai_vacancy_verdict = 'likely_' || ai_vacancy_verdict
where ai_vacancy_verdict in ('vacant', 'occupied');

alter table bills
  add constraint bills_ai_vacancy_verdict_check
  check (ai_vacancy_verdict in ('likely_vacant', 'likely_occupied', 'unknown'));
