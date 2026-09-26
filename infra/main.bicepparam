using 'main.bicep'

param prefix = 'fridgedash'
param staticWebAppLocation = 'eastus2'
param googleCalendarIcsUrl = readEnvironmentVariable('GOOGLE_CALENDAR_ICS_URL')
param deviceToken = readEnvironmentVariable('DEVICE_TOKEN')
