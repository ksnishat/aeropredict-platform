project_name            = "aeropredict"
environment             = "prod"
location                = "West Europe"
kubernetes_version      = "1.30"
node_count              = 3
vm_size                 = "Standard_D4s_v5"
postgres_admin_username = "pgadmin"
# postgres_admin_password is intentionally NOT set here. Supply it via the
# TF_VAR_postgres_admin_password environment variable (or a secret store):
#   export TF_VAR_postgres_admin_password="$(openssl rand -base64 24)"
tags = {
  "Project"     = "AeroPredict"
  "Environment" = "prod"
  "Owner"       = "Khaled Saifullah"
  "CostCenter"  = "MLOps-Prod"
}
