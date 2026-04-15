# This directory holds variable values for the dev environment.
# There is no deployable Terraform state here.
#
# To deploy:
#   Copy terraform.tfvars.example → terraform.tfvars and fill in TODO values.
#
#   Backend:
#     cd infrastructure/backend
#     terraform init
#     terraform apply -var-file="../environments/dev/terraform.tfvars"
#
#   Frontend:
#     cd infrastructure/frontend
#     terraform init
#     terraform apply -var-file="../environments/dev/terraform.tfvars"
