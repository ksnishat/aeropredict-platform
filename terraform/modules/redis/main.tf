# Redis Cache Module

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

# Redis Cache
resource "azurerm_redis_cache" "main" {
  name                = "${var.project_name}-${var.environment}-redis"
  location            = var.location
  resource_group_name = var.resource_group_name
  capacity            = 1
  family              = "C"
  sku_name            = "Standard"
  enable_non_ssl_port = false
  minimum_tls_version = "1.2"

  redis_configuration {
    maxmemory_reserved = 2
    maxmemory_delta    = 2
    maxmemory_policy   = "allkeys-lru"
  }

  tags = var.tags
}

output "hostname" {
  value       = azurerm_redis_cache.main.hostname
  description = "Redis hostname"
}

output "primary_key" {
  value       = azurerm_redis_cache.main.primary_key
  description = "Redis primary key"
  sensitive   = true
}