output "environment" {
  description = "Selected deployment environment."
  value       = var.environment
}

output "common_tags" {
  description = "Tags to pass to environment-specific modules."
  value       = local.common_tags
}