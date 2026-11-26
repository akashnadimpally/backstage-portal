resource "terraform_data" "module_context" {
  input = {
    name = var.name
    tags = var.tags
  }
}