using 'budget.bicep'

param amount = 100
param startDate = '2026-10-01'
// Kept out of the repo: export ARCHLENS_ALERT_EMAIL before deploying.
param alertEmail = readEnvironmentVariable('ARCHLENS_ALERT_EMAIL')
