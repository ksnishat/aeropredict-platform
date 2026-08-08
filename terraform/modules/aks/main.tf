# AKS Cluster Module

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

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
}

variable "node_count" {
  description = "Number of nodes"
  type        = number
}

variable "vm_size" {
  description = "VM size"
  type        = string
}

variable "tags" {
  description = "Tags"
  type        = map(string)
}

# AKS Cluster
resource "azurerm_kubernetes_cluster" "main" {
  name                = "${var.project_name}-${var.environment}-aks"
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_prefix          = "${var.project_name}-${var.environment}"
  kubernetes_version  = var.kubernetes_version
  node_resource_group = "${var.project_name}-${var.environment}-nodes-rg"

  default_node_pool {
    name                = "default"
    node_count          = var.node_count
    vm_size             = var.vm_size
    type                = "VirtualMachineScaleSets"
    enable_auto_scaling = true
    min_count           = 1
    max_count           = 5
    max_pods            = 110
    os_disk_size_gb     = 100
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin = "azure"
    service_cidr   = "10.100.0.0/16"
    dns_service_ip = "10.100.0.10"
    docker_bridge_cidr = "172.17.0.1/16"
  }

  tags = var.tags
}

# Container Registry
resource "azurerm_container_registry" "main" {
  name                = "${replace(var.project_name, "-", "")}${var.environment}acr"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = true

  tags = var.tags
}

output "kube_config" {
  value       = azurerm_kubernetes_cluster.main.kube_config_raw
  description = "Kubernetes configuration"
  sensitive   = true
}

output "kube_host" {
  value       = azurerm_kubernetes_cluster.main.kube_config[0].host
  description = "Kubernetes API server host"
}

output "acr_login_server" {
  value       = azurerm_container_registry.main.login_server
  description = "ACR login server"
}

output "acr_admin_username" {
  value       = azurerm_container_registry.main.admin_username
  description = "ACR admin username"
}

output "acr_admin_password" {
  value       = azurerm_container_registry.main.admin_password
  description = "ACR admin password"
  sensitive   = true
}