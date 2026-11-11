output "image" {
  description = "Image the Deployment runs."
  value       = local.image
}

output "refresh_api_key" {
  description = "Value for the x-api-key header of POST /api/news/refresh. Read with: terraform output -raw refresh_api_key"
  value       = random_password.refresh_api_key.result
  sensitive   = true
}
