# Terraform Infrastructure for AeroPredict

This directory contains Terraform configurations for deploying AeroPredict to Azure.

## Architecture

```
Azure Resource Group
├── AKS Cluster (Kubernetes)
│   ├── Default node pool (auto-scaling)
│   └── Container Registry (ACR)
├── PostgreSQL Flexible Server
│   ├── Database per project
│   └── Firewall rules
├── Redis Cache (Standard)
└── Monitoring
    ├── Log Analytics Workspace
    ├── Application Insights
    └── Action Group (alerts)
```

## Setup

```bash
cd terraform
terraform init
terraform plan -var-file=environments/dev/terraform.tfvars
terraform apply -var-file=environments/dev/terraform.tfvars
```

## Interview Talking Points

- "I use Terraform for Infrastructure as Code — all Azure resources are defined as code and version-controlled"
- "The modular design allows reusing the same infrastructure across projects"
- "Environment-specific configurations (dev vs prod) ensure consistent deployments"
