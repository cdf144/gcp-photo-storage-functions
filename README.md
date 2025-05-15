# GCP Photo Storage Functions

## Deployment

Việc triển khai các hàm này lên Google Cloud Run Functions được thực hiện thông qua `gcloud` CLI. Hướng dẫn cài đặt `gcloud` CLI có thể tham khảo tại [đây](https://cloud.google.com/sdk/docs/install).

> [!NOTE]
> Khi cài đặt `gcloud` CLI, làm theo hướng dẫn đến bước `gcloud init` để cấu hình tài khoản Google Cloud và project là xong.
>
> Bước `Do you want to configure a default Compute Region and Zone? (Y/n)?` có thể được bỏ qua (nhấn `n`), vì các lệnh deploy đã chỉ định region cụ thể là Singapore (asia-southeast1).

Tất cả các lệnh được đặt trong file `deploy.sh`, với mỗi lệnh ứng với một hàm trong mã nguồn được triển khai lên Google Cloud Run Functions. Mỗi hàm sau khi triển khai sẽ có một URL endpoint để gọi hàm đó.

Ví dụ, lệnh dưới đây sẽ triển khai hàm HTTP với tên là **upload-image-function** trên Google Cloud Run Functions gọi vào hàm `upload_image` trong mã nguồn, sử dụng base image là `python313` và cho phép truy cập public (không cần xác thực):

```sh
gcloud run deploy get-images-metadata-function \
  --source . \
  --base-image python313 \
  --region asia-southeast1 \
  --function get_images_metadata \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --set-env-vars BUCKET_NAME="photo-cloud-storage-bucket-1" \
  --allow-unauthenticated
```

Tương tự, dưới đây là lệnh triển khai hàm cloud event **delete-image-metadata** gọi vào hàm `delete_image_metadata` trong mã nguồn, phản ứng với sự kiện xóa một object trong bucket Google Cloud Storage **photo-cloud-storage-bucket-1** và xóa metadata tương ứng của object đó trong Firestore database **photo-cloud-storage-firestore**:

```sh
gcloud functions deploy delete-image-metadata \
  --runtime python313 \
  --region asia-southeast1 \
  --entry-point delete_image_metadata \
  --set-env-vars FIRESTORE_DATABASE="photo-cloud-storage-firestore" \
  --trigger-event google.cloud.storage.object.v1.deleted \
  --trigger-resource photo-cloud-storage-bucket-1
```

Khuyến nghị không chạy cả script `deploy.sh` trong một lần nếu không cần thiết. Chạy từng lệnh một để dễ theo dõi và xử lý lỗi nếu có.

## Development

Có 2 loại hàm chính trong mã nguồn này:

- **Hàm HTTP**: Được đặt trong `functions_http.py`. Các hàm này hoạt động theo cơ chế HTTP request/response thông thường, được gọi từ một client (browser, mobile app, etc.) thông qua một URL endpoint, và trả về HTTP response phù hợp.
- **Hàm Cloud Event**: Được đặt trong `functions_cloud_event.py`. Các hàm này được cấu hình để kích hoạt bởi Eventarc điều hướng các sự kiện từ Cloud Pub/Sub đến. Cơ chế phản ứng với sự kiện này mở đường cho việc cài đặt các hàm xử lý sự kiện từ các dịch vụ khác trong Google Cloud, như Google Cloud Storage, Firestore, v.v. Các hàm này không trả về HTTP response mà chỉ thực hiện các tác vụ cần thiết khi có sự kiện xảy ra.

Mỗi khi thay đổi mã nguồn, cần phải triển khai lại hàm tương ứng lên Google Cloud Run Functions để cập nhật mã nguồn mới.

Các hàm HTTP có thể được gọi từ một client (trình duyệt Web, Postman, v.v.) thông qua một URL endpoint, và trả về HTTP response phù hợp.

Các hàm Cloud Event không trả về HTTP response mà chỉ thực hiện các tác vụ cần thiết khi có sự kiện xảy ra. Có thể sử dụng các lệnh `print` để debug và theo dõi các sự kiện trong hàm Cloud Event. Nội dung được print ra sẽ được ghi lại trên trang **Logs** của hàm trên Google Cloud Run Function.
