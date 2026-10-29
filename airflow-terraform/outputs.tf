output "instance_id" {
  description = "EC2 instance ID (used for stop/start)"
  value       = aws_instance.airflow.id
}

output "instance_public_ip" {
  description = "Public IP of the Airflow instance (changes after stop/start)"
  value       = aws_instance.airflow.public_ip
}

output "airflow_url" {
  description = "Airflow web UI"
  value       = "http://${aws_instance.airflow.public_ip}:8080"
}

output "ssh_command" {
  description = "SSH into the instance"
  value       = "ssh -i ~/.ssh/sandbox_key ubuntu@${aws_instance.airflow.public_ip}"
}

output "rds_endpoint" {
  description = "Postgres endpoint used as the Airflow metadata DB"
  value       = aws_db_instance.airflow.endpoint
}

output "airflow_admin_username" {
  value = "admin"
}

output "airflow_admin_password" {
  description = "Show with: terraform output -raw airflow_admin_password"
  value       = random_password.airflow_admin.result
  sensitive   = true
}
