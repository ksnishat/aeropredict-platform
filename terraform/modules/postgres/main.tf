# PostgreSQL Module

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

variable "admin_username" {
  description = "PostgreSQL admin username"
  type        = string
}

variable "admin_password" {
  description = "PostgreSQL admin password"
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Tags"
  type        = map(string)
}

# PostgreSQL Server
resource "azurerm_postgresql_flexible_server" "main" {
  name                = "${var.project_name}-${var.environment}-pg"
  resource_group_name = var.resource_group_name
  location            = var.location
  version             = "15"

  administrator_login          = var.admin_username
  administrator_login_password = var.admin_password

  sku_name   = "B_Gen5_2"
  storage_mb = 20480

  backup {
    backup_retention_days = 7
    backup_interval_hours = 24
  }

  high_availability {
    mode = "Disabled"
  }

  maintenance_window {
    day_of_week = 0
    start_hour  = 2
    start_minute  = 0
  }

  tags = var.tags
}

# PostgreSQL Database
resource "azurerm_postgresql_flexible_server_database" "main" {
  name                = "${var.project_name}_${var.environment}"
  resource_group_name = var.resource_group_name
  server_name         = azurerm_postgresql_flexible_server.main.name
  charset             = "UTF8"
  collation           = "en_US.UTF8"
}

# Firewall rule for AKS
resource "azurerm_postgresql_flexible_server_firewall_rule" "aks" {
  name             = "allow-aks"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

output "connection_string" {
  value       = "postgresql://${var.admin_username}:${var.admin_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/${azurerm_postgresql_flexible_server_database.main.name}"
  description = "PostgreSQL connection string"
  sensitive   = true
}

output "server_fqdn" {
  value       = azurerm_postgresql_flexible_server.main.fqdn
  description = "PostgreSQL server FQDN"
}