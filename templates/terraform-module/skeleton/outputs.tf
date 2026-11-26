output "name" {
  description = "Name provided to the module."
  value       = terraform_data.module_context.output.name
}

output "tags" {
  description = "Tags provided to the module."
  value       = terraform_data.module_context.output.tags
}