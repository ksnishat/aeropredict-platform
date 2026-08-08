# Monitoring Module (Log Analytics + Application Insights)

variable "project_name" {
  description = "Name of the ML project"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
}

variable "location" {
  description = "Azure region"
  type        = string
}

variable "tags" {
  description = "Tags"
  type        = map(string)
}

# Log Analytics Workspace
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.project_name}-${var.environment}-logs"
  location            = var.location
  resource_group_name = var.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = var.tags
}

# Application Insights
resource "azurerm_application_insights" "main" {
  name                = "${var.project_name}-${var.environment}-ai"
  location            = var.location
  resource_group_name = var.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"

  tags = var.tags
}

# Action Group for alerts
resource "azurerm_monitor_action_group" "main" {
  name                = "${var.project_name}-${var.environment}-alerts"
  resource_group_name = var.resource_group_name
  short_name          = "alerts"

  email_receiver {
    name                    = "admin"
    email_address           = "admin@example.com"
    use_common_alert_schema = true
  }

  tags = var.tags
}

output "workspace_id" {
  value       = azurerm_log_analytics_workspace.main.id
  description = "Log Analytics workspace ID"
}

output "instrumentation_key" {
  value       = azurerm_application_insights.main.instrumentation_key
  description = "Application Insights instrumentation key"
  sensitive   = true
}

output "action_group_id" {
  value       = azurerm_monitor_action_group.main.id
  description = "Action group ID for alerts"
}