// Subscription budget with email alerts (docs/AZURE.md §0.3). Deploy before any other resource.
// Alerts never stop spending; the app's per-run and per-eval budget guards do.
targetScope = 'subscription'

@description('Budget amount in USD (the Azure for Students credit).')
param amount int = 100

@description('Address that receives the alerts.')
param alertEmail string

@description('First day of the month the budget counts from (yyyy-MM-01). Fixed, not utcNow(): a budget start date cannot move on redeploy.')
param startDate string

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'archlens-credit'
  properties: {
    category: 'Cost'
    amount: amount
    // Annually, not Monthly: the credit is a one-off amount, so spend must accumulate across the
    // whole project instead of resetting every month.
    timeGrain: 'Annually'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actual60: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 60
        thresholdType: 'Actual'
        contactEmails: [alertEmail]
      }
      actual85: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 85
        thresholdType: 'Actual'
        contactEmails: [alertEmail]
      }
    }
  }
}

output budgetName string = budget.name
