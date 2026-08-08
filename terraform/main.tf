# Terraform configuration for AeroPredict Azure infrastructure
# Deploys AKS cluster, PostgreSQL, Redis, and monitoring

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
  }

  backend "azurerm" {
    resource_group_name  = "tfstate-rg"
    storage_account_name = "tfstateaccount"
    container_name       = "tfstate"
    key                  = "aeropredict.terraform.tfstate"
  }
}

provider "azurerm" {
  features {}
}

# --- Variables ---

variable "project_name" {
  description = "Name of the ML project"
  type        = string
  default     = "aeropredict"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "West Europe"
}

variable "kubernetes_version" {
  description = "AKS Kubernetes version"
  type        = string
  default     = "1.30"
}

variable "node_count" {
  description = "Number of AKS nodes"
  type        = number
  default     = 2
}

variable "vm_size" {
  description = "VM size for AKS nodes"
  type        = string
  default     = "Standard_B2ms"
}

variable "postgres_admin_username" {
  description = "PostgreSQL admin username"
  type        = string
  default     = "pgadmin"
}

variable "postgres_admin_password" {
  description = "PostgreSQL admin password"
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    "Project"     = "AeroPredict"
    "Environment" = "dev"
    "Owner"       = "Khaled Saifullah"
  }
}

# --- Resource Group ---

resource "azurerm_resource_group" "main" {
  name     = "${var.project_name}-${var.environment}-rg"
  location = var.location
  tags     = var.tags
}

# --- AKS Cluster ---

module "aks" {
  source = "./modules/aks"

  project_name         = var.project_name
  environment          = var.environment
  resource_group_name  = azurerm_resource_group.main.name
  location             = var.location
  kubernetes_version   = var.kubernetes_version
  node_count           = var.node_count
  vm_size              = var.vm_size
  tags                 = var.tags
}

# --- PostgreSQL ---

module "postgres" {
  source = "./modules/postgres"

  project_name           = var.project_name
  environment            = var.environment
  resource_group_name    = azurerm_resource_group.main.name
  location               = var.location
  admin_username         = var.postgres_admin_username
  admin_password         = var.postgres_admin_password
  tags                   = var.tags
}

# --- Redis Cache ---

module "redis" {
  source = "./modules/redis"

  project_name      = var.project_name
  environment       = var.environment
  resource_group_name = azurerm_resource_group.main.name
  location          = var.location
  tags              = var.tags
}

# --- Monitoring ---

module "monitoring" {
  source = "./modules/monitoring"

  project_name      = var.project_name
  environment       = var.environment
  resource_group_name = azurerm_resource_group.main.name
  location          = var.location
  tags              = var.tags
}

# --- Outputs ---

output "kube_config" {
  value       = module.aks.kube_config
  description = "Kubernetes configuration"
  sensitive   = true
}

output "postgres_connection_string" {
  value       = module.postgres.connection_string
  description = "PostgreSQL connection string"
  sensitive   = true
}

output "redis_hostname" {
  value       = module.redis.hostname
  description = "Redis hostname"
}

output "monitoring_workspace_id" {
  value       = module.monitoring.workspace_id
  description = "Log Analytics workspace ID"
}
