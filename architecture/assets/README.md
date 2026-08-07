# Diagram icons

The architecture diagram (`../architecture-diagram.html`) references SVG icons from this folder. The folder is self-contained — no external paths.

## Inventory

| File | Source | Used for |
| --- | --- | --- |
| `users.svg` | (provided) | Browser / end user |
| `cognito.svg` | (provided) | Amazon Cognito User Pool |
| `waf.svg` | (provided) | AWS WAF v2 |
| `cloudfront.svg` | (provided) | Amazon CloudFront |
| `elb.svg` | (provided) | Application Load Balancer |
| `ecs.svg` | (provided) | ECS Fargate service |
| `s3.svg` | (provided) | S3 bucket |
| `secrets-manager.svg` | (provided) | Secrets Manager (CF custom-header reference) |
| `bedrock.svg` | (provided) | Bedrock foundation models |
| `bedrock-agentcore.svg` | (provided) | Bedrock AgentCore Runtime container header |
| `lambda.svg` | (provided) | TiTiler Lambda function |
| `api-gateway.svg` | (provided) | API Gateway in front of TiTiler |
| `amazon-location.svg` | AWS pack — `Arch_Front-End-Web-Mobile/64/Arch_Amazon-Location-Service_64.svg` | Amazon Location Service (via bundled MCP server) |
| `satellite.svg` | AWS pack — `Category-Icons_04302026/Arch-Category_64/Arch-Category_Satellite_64.svg` | Sentinel-2 / STAC catalog node (generic Satellite category icon) |
| `osm.svg` | AWS pack — `Resource-Icons_04302026/Res_General-Icons/Res_48_Dark/Res_Globe_48_Dark.svg` | OpenStreetMap (generic globe icon as a stand-in) |
| `cloudwatch.svg` | AWS pack — `Arch_Management-Tools/64/Arch_Amazon-CloudWatch_64.svg` | CloudWatch + ADOT observability node |

## Style

If you swap any icon, keep:

- SVG, square viewBox (32 × 32 or 64 × 64 work best at the diagram's render size).
- AWS-canonical brand colors against a transparent background.
- Roughly the same visual weight as the existing icons (the diagram doesn't normalize size beyond the `width` / `height` on each `<image>`).
