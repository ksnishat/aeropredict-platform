project_name            = "aeropredict"
environment             = "dev"
location                = "West Europe"
kubernetes_version      = "1.30"
node_count              = 1
vm_size                 = "Standard_B2ms"
postgres_admin_username = "pgadmin"
# postgres_admin_password is intentionally NOT set here. Supply it via the
# TF_VAR_postgres_admin_password environment variable (or a secret store):
#   export TF_VAR_postgres_admin_password="$(openssl rand -base64 24)"
tags = {
  "Project"     = "AeroPredict"
  "Environment" = "dev"
  "Owner"       = "Khaled Saifullah"
  "CostCenter"  = "MLOps-Dev"
}
