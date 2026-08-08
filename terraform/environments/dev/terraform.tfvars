project_name           = "aeropredict"
environment            = "dev"
location               = "West Europe"
kubernetes_version     = "1.30"
node_count             = 1
vm_size                = "Standard_B2ms"
postgres_admin_username = "pgadmin"
postgres_admin_password = "ChangeMeInDev123!"

tags = {
  "Project"     = "AeroPredict"
  "Environment" = "dev"
  "Owner"       = "Khaled Saifullah"
  "CostCenter"  = "MLOps-Dev"
}
